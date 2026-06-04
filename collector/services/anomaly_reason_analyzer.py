from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import requests

from collector.services.ai_summary_client import is_safe_ai_base_url, normalize_ai_text


logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个中文财经“证据约束型异动归因”助手。

你的任务不是预测行情，也不是总结所有新闻，而是判断输入材料是否能解释某只股票的当日显著异动。

必须严格遵守：
1. 只能使用输入中的【异动信息】【库内新闻】【库内公告】【库内研报】【库内盘面摘要】【龙虎榜/机构席位】，不能引入任何外部知识、常识补全或行业臆测。
2. 只有当新闻、公告、研报等材料明确提到公司、业务、订单、业绩、公告、评级、监管、事件进展等事实，且这些事实与异动方向/量能存在可解释联系时，才可给出“可能与……有关”。
3. 【库内盘面摘要】和【龙虎榜/机构席位】只能作为现象和线索，不能单独写成确定因果；若缺少明确资讯证据，只能写“未发现明确资讯触发证据，盘面显示……”或“原因仍待进一步确认”。
4. 如果材料只是泛市场、泛行业、ETF、指数、资金榜、无正文标题，或无法建立清晰联系，必须保留不确定性。
5. 不得把“提及某公司”改写成“导致上涨/下跌”；除非材料明确说明影响方向，否则只能写“可能受到市场关注”。
6. 不得出现买入、卖出、持有、推荐、目标价、加仓、减仓、抄底、止盈、止损等投资建议或操作暗示。
7. 不预测未来走势，不承诺收益，不做风险评级。
8. 输出 1 到 2 句中文，总长度不超过 120 个中文字符。"""


USER_PROMPT_TEMPLATE = """请为以下股票异动生成安全的可能原因说明。

【异动信息】
股票代码：{symbol}
异动类型：{anomaly_type}
严重程度：{severity}
涨跌幅/跳变：{change_pct}
量比：{volume_ratio}
触发时间：{trigger_time}
事件数：{event_count}

【库内新闻】
{news_context}

【库内公告】
{announcement_context}

【库内研报】
{report_context}

【库内盘面摘要】
{market_context}

【龙虎榜/机构席位】
{dragon_tiger_context}

判断步骤：
1. 先区分“明确资讯证据”与“盘面/席位线索”。
2. 只有明确资讯证据直接关联该股票代码/公司时，才可写“可能与……有关”。
3. 若只有盘面/席位线索，没有明确资讯证据，只能写“未发现明确资讯触发证据，盘面显示……”或“原因仍待进一步确认”。
4. 如果任一步不成立，保持不确定，不得强行补全原因。

输出要求：
1. 只输出归因正文，不要输出标题、标签、项目符号或解释过程。
2. 如果材料没有明确原因，可以输出“原因待确认”，也可以输出“未发现明确资讯触发证据，盘面显示……，原因仍待进一步确认”。
3. 若可归因，使用“可能与……有关”或“异动可能受到……影响”的保守表述。
4. 若没有明确资讯原因，但盘面/席位信号较明显，可以输出“未发现明确资讯触发证据，盘面显示……，原因仍待进一步确认”。
5. 不要出现买入、卖出、持有、推荐、目标价等投资建议。"""

FORBIDDEN_ADVICE_TERMS = ("买入", "卖出", "持有", "推荐", "目标价", "加仓", "减仓", "抄底", "止盈", "止损")


@dataclass(slots=True)
class AnomalyReasonResult:
    reason: str | None
    status: str
    related_news_ids: list[int]
    related_announcement_ids: list[int]
    llm_succeeded: bool = False
    attempted: bool = False
    skip_reason: str | None = None
    model_used: str | None = None
    prompt_version: str = "v1"
    latency_ms: int | None = None


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (float, int, Decimal)):
        return float(value)
    return None


def _format_value(value: object) -> str:
    if value is None:
        return "--"
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.replace(tzinfo=UTC).isoformat()
    return str(value)


def _format_signed_percent(value: object) -> str:
    number = _to_float(value)
    if number is None:
        return "--"
    return f"{number:+.2f}%"


def _format_price(value: object) -> str:
    number = _to_float(value)
    if number is None:
        return "--"
    return f"{number:.2f}"


def _format_amount_short(value: object) -> str:
    number = _to_float(value)
    if number is None:
        return "--"
    absolute_number = abs(number)
    if absolute_number >= 100000000:
        return f"{number / 100000000:.2f}亿元"
    if absolute_number >= 10000:
        return f"{number / 10000:.2f}万元"
    return f"{number:.0f}元"


def _format_volume_short(value: object) -> str:
    number = _to_float(value)
    if number is None:
        return "--"
    absolute_number = abs(number)
    if absolute_number >= 100000000:
        return f"{number / 100000000:.2f}亿股"
    if absolute_number >= 10000:
        return f"{number / 10000:.2f}万股"
    return f"{number:.0f}股"


def _field(row: object, key: str, default: object = None) -> object:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _row_id(row: object) -> int | None:
    try:
        value = row["id"]
    except Exception:
        return None
    return int(value) if value is not None else None


def _clip(value: object, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}…"


def _contains_advice(text: str) -> bool:
    return any(term in text for term in FORBIDDEN_ADVICE_TERMS)


def _looks_like_prompt_echo(text: str) -> bool:
    normalized = text.strip()
    if normalized.startswith(("1.", "2.", "3.", "- ", "•")):
        return True
    return any(marker in normalized for marker in ("异动类型", "严重程度", "输出要求", "判断步骤"))


def _looks_low_information_result(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return True
    return normalized.startswith(("股票代码：", "异动类型：", "严重程度：", "涨跌幅/跳变：", "量比：", "触发时间：", "事件数："))


class AnomalyReasonAnalyzer:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._last_latency_ms: int | None = None

    def is_configured(self) -> bool:
        return bool(
            self._settings.anomaly_ai_reason_enabled
            and self._settings.content_ai_summary_enabled
            and self._settings.content_ai_summary_base_url
            and self._settings.content_ai_summary_api_key
            and self._settings.content_ai_summary_model
            and is_safe_ai_base_url(self._settings.content_ai_summary_base_url)
        )

    def reason_window(self, trigger_ts: datetime) -> tuple[datetime, datetime]:
        normalized_ts = trigger_ts.astimezone(UTC) if trigger_ts.tzinfo else trigger_ts.replace(tzinfo=UTC)
        return normalized_ts - timedelta(days=3), datetime.now(UTC) + timedelta(minutes=5)

    def analyze(self, anomaly, context: dict[str, list[object]]) -> AnomalyReasonResult:
        prompt_version = str(self._settings.content_ai_summary_prompt_version or "v1")
        if not self._settings.anomaly_ai_reason_enabled:
            return AnomalyReasonResult(reason=None, status="skipped", related_news_ids=[], related_announcement_ids=[], skip_reason="config_disabled", prompt_version=prompt_version)
        if not self.is_configured():
            logger.warning("anomaly ai reason config incomplete or unsafe")
            return AnomalyReasonResult(reason=None, status="skipped", related_news_ids=[], related_announcement_ids=[], skip_reason="missing_model_config", prompt_version=prompt_version)

        news_rows = context.get("news", [])
        announcement_rows = context.get("announcements", [])
        report_rows = context.get("reports", [])
        market_summary = context.get("market_summary") if isinstance(context.get("market_summary"), dict) else None
        dragon_tiger_daily = context.get("dragon_tiger_daily") if isinstance(context.get("dragon_tiger_daily"), dict) else None
        dragon_tiger_institution = context.get("dragon_tiger_institution") if isinstance(context.get("dragon_tiger_institution"), dict) else None
        fallback_reason = self._build_evidence_bound_fallback(anomaly, context)
        if not news_rows and not announcement_rows and not report_rows and not market_summary and not dragon_tiger_daily and not dragon_tiger_institution:
            return AnomalyReasonResult(reason=fallback_reason, status="completed", related_news_ids=[], related_announcement_ids=[], skip_reason="no_supporting_context", prompt_version=prompt_version)

        prompt = USER_PROMPT_TEMPLATE.format(
            symbol=anomaly["symbol"],
            anomaly_type=anomaly["anomaly_type"],
            severity=anomaly["severity"],
            change_pct=_format_value(_to_float(_field(anomaly, "change_pct"))),
            volume_ratio=_format_value(_to_float(_field(anomaly, "volume_ratio"))),
            trigger_time=_format_value(anomaly["first_trigger_ts"]),
            event_count=_format_value(_field(anomaly, "event_count")),
            news_context=self._format_news(news_rows),
            announcement_context=self._format_announcements(announcement_rows),
            report_context=self._format_reports(report_rows),
            market_context=self._format_market_summary(market_summary),
            dragon_tiger_context=self._format_dragon_tiger(dragon_tiger_daily, dragon_tiger_institution),
        )
        result = self._call_model(prompt)
        if result is None:
            if fallback_reason != "原因待确认":
                return AnomalyReasonResult(
                    reason=fallback_reason,
                    status="completed",
                    related_news_ids=[item for item in (_row_id(row) for row in news_rows) if item is not None],
                    related_announcement_ids=[item for item in (_row_id(row) for row in announcement_rows) if item is not None],
                    attempted=True,
                    llm_succeeded=False,
                    model_used=str(self._settings.content_ai_summary_model),
                    prompt_version=prompt_version,
                    latency_ms=self._last_latency_ms,
                )
            return AnomalyReasonResult(
                reason=None,
                status="failed",
                related_news_ids=[item for item in (_row_id(row) for row in news_rows) if item is not None],
                related_announcement_ids=[item for item in (_row_id(row) for row in announcement_rows) if item is not None],
                attempted=True,
                llm_succeeded=False,
                model_used=str(self._settings.content_ai_summary_model),
                prompt_version=prompt_version,
                latency_ms=self._last_latency_ms,
            )
        if _contains_advice(result) or _looks_like_prompt_echo(result) or _looks_low_information_result(result):
            logger.warning("anomaly ai reason failed safety/quality checks; using safe fallback", extra={"symbol": anomaly["symbol"]})
            result = fallback_reason
        if result.strip() == "原因待确认" and fallback_reason != "原因待确认":
            result = fallback_reason
        return AnomalyReasonResult(
            reason=result,
            status="completed",
            related_news_ids=[item for item in (_row_id(row) for row in news_rows) if item is not None],
            related_announcement_ids=[item for item in (_row_id(row) for row in announcement_rows) if item is not None],
            attempted=True,
            llm_succeeded=True,
            model_used=str(self._settings.content_ai_summary_model),
            prompt_version=prompt_version,
            latency_ms=self._last_latency_ms,
        )

    def _call_model(self, user_prompt: str) -> str | None:
        self._last_latency_ms = None
        payload = {
            "model": self._settings.content_ai_summary_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._settings.content_ai_summary_temperature,
            "max_completion_tokens": min(max(self._settings.content_ai_summary_max_completion_tokens, 256), 1024),
            "reasoning_split": True,
            "stream": False,
        }
        url = f"{self._settings.content_ai_summary_base_url.rstrip('/')}/chat/completions"
        timeout = max(self._settings.content_ai_summary_timeout_seconds, 1)
        retries = max(self._settings.content_ai_summary_max_retries, 0)
        for attempt in range(retries + 1):
            try:
                started_at = time.monotonic()
                response = requests.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._settings.content_ai_summary_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=timeout,
                )
                if response.status_code >= 400:
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        raise requests.HTTPError(f"anomaly ai reason upstream error {response.status_code}")
                    response.raise_for_status()
                data = response.json()
                self._last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                choices = data.get("choices") if isinstance(data, dict) else None
                if not isinstance(choices, list) or not choices:
                    return None
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                return normalize_ai_text(content)
            except Exception as exc:
                self._last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                if attempt >= retries:
                    logger.warning("anomaly ai reason request failed", exc_info=exc)
                    return None
                time.sleep(min(2 ** attempt, 8))
        return None

    @staticmethod
    def _format_news(rows: list[object]) -> str:
        if not rows:
            return "无"
        lines = []
        for index, row in enumerate(rows, start=1):
            summary = row["ai_summary"] or row["summary"] or row["content"]
            lines.append(
                f"{index}. 标题：{_clip(row['title'], 120)}；来源：{_clip(row['article_source'], 40)}；时间：{_format_value(row['first_seen_at'])}；内容：{_clip(summary, 280)}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_announcements(rows: list[object]) -> str:
        if not rows:
            return "无"
        lines = []
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"{index}. 标题：{_clip(row['title'], 140)}；类型：{_clip(row['announcement_type'], 40)}；时间：{_format_value(row['first_seen_at'])}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_reports(rows: list[object]) -> str:
        if not rows:
            return "无"
        lines = []
        for index, row in enumerate(rows, start=1):
            lines.append(
                f"{index}. 标题：{_clip(row['title'], 140)}；评级：{_clip(row['rating'], 40)}；机构：{_clip(row['institution'], 60)}；行业：{_clip(row['industry'], 60)}；时间：{_format_value(row['first_seen_at'])}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_market_summary(summary: dict[str, object] | None) -> str:
        if not summary:
            return "无"
        lines = []
        if any(summary.get(key) is not None for key in ("open_price", "high_price", "low_price", "last_price")):
            lines.append(
                "1. 日内价格：开 {open_price}；高 {high_price}；低 {low_price}；最新 {last_price}".format(
                    open_price=_format_price(summary.get("open_price")),
                    high_price=_format_price(summary.get("high_price")),
                    low_price=_format_price(summary.get("low_price")),
                    last_price=_format_price(summary.get("last_price")),
                )
            )
        lines.append(
            "2. 涨跌幅：{change_pct}；振幅：{amplitude}；换手率：{turnover}".format(
                change_pct=_format_signed_percent(summary.get("snapshot_change_pct")),
                amplitude=_format_signed_percent(summary.get("amplitude_pct")),
                turnover=_format_signed_percent(summary.get("turnover_rate")),
            )
        )
        lines.append(
            "3. 成交量：{volume}；成交额：{amount}".format(
                volume=_format_volume_short(summary.get("session_volume")),
                amount=_format_amount_short(summary.get("session_amount")),
            )
        )
        dominant_side = summary.get("dominant_side")
        if dominant_side == "buy":
            side_text = "逐笔方向偏买盘"
        elif dominant_side == "sell":
            side_text = "逐笔方向偏卖盘"
        elif dominant_side == "balanced":
            side_text = "逐笔方向相对均衡"
        else:
            side_text = "逐笔方向未知"
        lines.append(
            "4. {side_text}；近 5 日区间涨跌：{recent_change}".format(
                side_text=side_text,
                recent_change=_format_signed_percent(summary.get("recent_5d_change_pct")),
            )
        )
        return "\n".join(lines)

    @staticmethod
    def _format_dragon_tiger(daily_row: dict[str, object] | None, institution_row: dict[str, object] | None) -> str:
        lines = []
        if daily_row:
            lines.append(
                "1. 龙虎榜：日期 {trade_date}；净买额 {net_buy}；上榜原因 {reason}；说明 {explain}".format(
                    trade_date=_format_value(daily_row.get("trade_date")),
                    net_buy=_format_amount_short(daily_row.get("net_buy_amount")),
                    reason=_clip(daily_row.get("reason"), 60),
                    explain=_clip(daily_row.get("explain"), 60),
                )
            )
        if institution_row:
            lines.append(
                "{prefix}. 机构席位：日期 {trade_date}；买入席位 {buy_count}；卖出席位 {sell_count}；机构净额 {net_amount}；原因 {reason}".format(
                    prefix=len(lines) + 1,
                    trade_date=_format_value(institution_row.get("trade_date")),
                    buy_count=_format_value(_field(institution_row, "buy_org_count")),
                    sell_count=_format_value(_field(institution_row, "sell_org_count")),
                    net_amount=_format_amount_short(institution_row.get("org_net_amount")),
                    reason=_clip(institution_row.get("reason"), 60),
                )
            )
        return "\n".join(lines) if lines else "无"

    @staticmethod
    def _build_evidence_bound_fallback(anomaly, context: dict[str, list[object]]) -> str:
        market_summary = context.get("market_summary") if isinstance(context.get("market_summary"), dict) else None
        dragon_tiger_daily = context.get("dragon_tiger_daily") if isinstance(context.get("dragon_tiger_daily"), dict) else None
        dragon_tiger_institution = context.get("dragon_tiger_institution") if isinstance(context.get("dragon_tiger_institution"), dict) else None

        evidence_fragments: list[str] = []
        if market_summary:
            market_bits: list[str] = []
            amplitude_pct = _to_float(market_summary.get("amplitude_pct"))
            session_amount = _to_float(market_summary.get("session_amount"))
            turnover_rate = _to_float(market_summary.get("turnover_rate"))
            dominant_side = market_summary.get("dominant_side")
            if isinstance(amplitude_pct, float):
                market_bits.append(f"振幅约{amplitude_pct:.1f}%")
            if isinstance(session_amount, float):
                market_bits.append(f"成交额{_format_amount_short(session_amount)}")
            if isinstance(turnover_rate, float):
                market_bits.append(f"换手率{turnover_rate:.2f}%")
            if dominant_side == "buy":
                market_bits.append("逐笔买盘占优")
            elif dominant_side == "sell":
                market_bits.append("逐笔卖盘占优")
            if market_bits:
                evidence_fragments.append("盘面" + "、".join(market_bits))

        if dragon_tiger_institution:
            org_net_amount = _to_float(dragon_tiger_institution.get("org_net_amount"))
            if isinstance(org_net_amount, float) and org_net_amount != 0:
                trade_date_text = _format_value(dragon_tiger_institution.get("trade_date"))
                evidence_fragments.append(
                    f"机构席位({trade_date_text})净{'买入' if org_net_amount > 0 else '卖出'}{_format_amount_short(abs(org_net_amount))}"
                )
        elif dragon_tiger_daily:
            net_buy_amount = _to_float(dragon_tiger_daily.get("net_buy_amount"))
            if isinstance(net_buy_amount, float) and net_buy_amount != 0:
                trade_date_text = _format_value(dragon_tiger_daily.get("trade_date"))
                evidence_fragments.append(
                    f"龙虎榜({trade_date_text})净{'买入' if net_buy_amount > 0 else '卖出'}{_format_amount_short(abs(net_buy_amount))}"
                )

        if not evidence_fragments:
            return "原因待确认"
        return f"未发现明确资讯触发证据，{'；'.join(evidence_fragments)}，原因仍待进一步确认。"
