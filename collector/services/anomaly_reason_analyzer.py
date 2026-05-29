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
1. 只能使用输入中的【异动信息】【库内新闻】【库内公告】【库内研报】，不能引入任何外部知识、常识补全或行业臆测。
2. 只有当材料明确提到公司、业务、订单、业绩、公告、评级、监管、事件进展等事实，且这些事实与异动方向/量能存在可解释联系时，才可给出“可能与……有关”。
3. 如果材料只是泛市场、泛行业、ETF、指数、资金榜、无正文标题，或无法建立清晰联系，必须输出“原因待确认”。
4. 不得把“提及某公司”改写成“导致上涨/下跌”；除非材料明确说明影响方向，否则只能写“可能受到市场关注”。
5. 不得出现买入、卖出、持有、推荐、目标价、加仓、减仓、抄底、止盈、止损等投资建议或操作暗示。
6. 不预测未来走势，不承诺收益，不做风险评级。
7. 输出 1 到 2 句中文，总长度不超过 120 个中文字符。"""


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

判断步骤：
1. 先判断材料是否直接关联该股票代码/公司。
2. 再判断材料是否提供了可解释异动的具体事实。
3. 如果任一步不成立，只输出“原因待确认”。

输出要求：
1. 只输出归因正文，不要输出标题、标签、项目符号或解释过程。
2. 如果材料没有明确原因，只输出“原因待确认”。
3. 若可归因，使用“可能与……有关”或“异动可能受到……影响”的保守表述。
4. 不要出现买入、卖出、持有、推荐、目标价等投资建议。"""

FORBIDDEN_ADVICE_TERMS = ("买入", "卖出", "持有", "推荐", "目标价", "加仓", "减仓", "抄底", "止盈", "止损")


@dataclass(slots=True)
class AnomalyReasonResult:
    reason: str | None
    status: str
    related_news_ids: list[int]
    related_announcement_ids: list[int]


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


class AnomalyReasonAnalyzer:
    def __init__(self, settings) -> None:
        self._settings = settings

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
        if not self._settings.anomaly_ai_reason_enabled:
            return AnomalyReasonResult(reason=None, status="skipped", related_news_ids=[], related_announcement_ids=[])
        if not self.is_configured():
            logger.warning("anomaly ai reason config incomplete or unsafe")
            return AnomalyReasonResult(reason=None, status="skipped", related_news_ids=[], related_announcement_ids=[])

        news_rows = context.get("news", [])
        announcement_rows = context.get("announcements", [])
        report_rows = context.get("reports", [])
        if not news_rows and not announcement_rows and not report_rows:
            return AnomalyReasonResult(reason="原因待确认", status="completed", related_news_ids=[], related_announcement_ids=[])

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
        )
        result = self._call_model(prompt)
        if result is None:
            return AnomalyReasonResult(
                reason=None,
                status="failed",
                related_news_ids=[item for item in (_row_id(row) for row in news_rows) if item is not None],
                related_announcement_ids=[item for item in (_row_id(row) for row in announcement_rows) if item is not None],
            )
        if _contains_advice(result) or _looks_like_prompt_echo(result):
            logger.warning("anomaly ai reason failed safety/quality checks; using safe fallback", extra={"symbol": anomaly["symbol"]})
            result = "原因待确认"
        return AnomalyReasonResult(
            reason=result,
            status="completed",
            related_news_ids=[item for item in (_row_id(row) for row in news_rows) if item is not None],
            related_announcement_ids=[item for item in (_row_id(row) for row in announcement_rows) if item is not None],
        )

    def _call_model(self, user_prompt: str) -> str | None:
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
                choices = data.get("choices") if isinstance(data, dict) else None
                if not isinstance(choices, list) or not choices:
                    return None
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                return normalize_ai_text(content)
            except Exception as exc:
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
