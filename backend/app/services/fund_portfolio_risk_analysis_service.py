from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import requests

from app.services.llm_capabilities import LLM_MAX_INPUT_CHARS, LLM_REQUEST_MAX_RETRIES, build_ai_headers, build_llm_attempt_meta, build_openai_chat_payload, extract_chat_message_text, get_ai_base_url, get_ai_model_candidates, get_ai_request_timeout_seconds, is_shared_llm_configured


logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
FORBIDDEN_ADVICE_TERMS = ("买入", "卖出", "推荐", "目标价", "加仓", "减仓", "抄底", "止盈", "止损")
FORBIDDEN_ADVICE_PATTERNS = (
    re.compile(r"(建议|可以|可考虑|适合|应该|应当|继续|保持|选择|值得)\s*持有"),
    re.compile(r"持有\s*(建议|评级|信号|策略)"),
)

SYSTEM_PROMPT = """你是 MoneyRush 的中文基金风险解读助手。

你的任务是基于输入的基金监控组合视角数据，解释当前观察池中的结构性风险。

必须严格遵守：
1. 只能使用输入 payload 中的字段，不引入外部事实、新闻或市场判断。
2. 不输出买入、卖出、持有、推荐、目标价、加仓、减仓、抄底、止盈、止损等投资建议或操作暗示。
3. 必须明确说明这是基于最近披露重仓与等权观察池假设的解释，不是用户真实持仓结论。
4. 输出必须是 JSON 对象，不要输出 Markdown、解释过程或额外文本。
5. 语言使用中文，语气保守，强调这是风险观察而非投资建议。"""

USER_PROMPT_TEMPLATE = """请基于以下基金监控组合视角数据生成风险解读。

【分析焦点】{focus}
【详细程度】{depth}

【必须遵守的派生事实】
{derived_facts}

【输入 JSON】
{snapshot_json}

请只输出一个 JSON 对象，字段如下：
{{
  "summary": "不超过120个中文字符的摘要",
  "riskLevel": "low | medium | high",
  "drivers": ["风险驱动1", "风险驱动2"],
  "watchItems": ["继续观察项1", "继续观察项2"],
  "limitations": ["限制说明1", "限制说明2"],
  "confidence": 0.0,
  "focus": "{focus}",
  "depth": "{depth}"
}}"""


@dataclass(slots=True)
class FundPortfolioRiskAnalysisResult:
    analysis: dict[str, object] | None
    model_used: str | None
    prompt_version: str
    attempted: bool = False
    skip_reason: str | None = None
    latency_ms: int | None = None
    attempts: list[dict[str, object]] | None = None


def _strip_reasoning_text(value: object) -> str | None:
    text = _coerce_content_text(value)
    if text is None:
        return None
    text = _THINK_BLOCK_RE.sub("", text).strip()
    return text or None


def _coerce_content_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = str(value.get("text") or value.get("content") or "")
    elif isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            part = item.get("text") or item.get("content")
            if isinstance(part, str) and part.strip():
                parts.append(part)
        text = "\n".join(parts)
    else:
        text = str(value)
    normalized = text.strip()
    return normalized or None


def _extract_json_object(value: object) -> dict[str, object] | None:
    text = _strip_reasoning_text(value)
    if not text:
        return None
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(candidate: str | None) -> None:
        normalized = str(candidate or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    add_candidate(text)
    for match in _CODE_FENCE_RE.finditer(text):
        add_candidate(match.group(1))
    for source in tuple(candidates):
        for fragment in _extract_json_fragments(source):
            add_candidate(fragment)
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, str):
            try:
                decoded = json.loads(decoded)
            except json.JSONDecodeError:
                continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _extract_json_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    start_index: int | None = None
    depth = 0
    in_string = False
    escaping = False
    for index, char in enumerate(text):
        if start_index is None:
            if char == "{":
                start_index = index
                depth = 1
                in_string = False
                escaping = False
            continue
        if in_string:
            if escaping:
                escaping = False
            elif char == "\\":
                escaping = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char != "}":
            continue
        depth -= 1
        if depth == 0:
            fragments.append(text[start_index : index + 1])
            start_index = None
    return fragments


def _contains_forbidden_advice(value: object) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, dict) else str(value or "")
    return any(term in text for term in FORBIDDEN_ADVICE_TERMS) or any(pattern.search(text) for pattern in FORBIDDEN_ADVICE_PATTERNS)


def _clip_text(value: object, max_chars: int, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:max_chars]


def _normalize_level(value: object) -> str:
    text = str(value or "low").strip().lower()
    return text if text in {"low", "medium", "high"} else "low"


def _normalize_text_list(value: object, *, fallback: list[str], item_max_chars: int, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return fallback
    normalized = [_clip_text(item, item_max_chars, "") for item in value]
    normalized = [item for item in normalized if item]
    return normalized[:max_items] or fallback


def _build_analysis_input(portfolio_view: dict[str, object]) -> dict[str, object]:
    summary = portfolio_view.get("summary") if isinstance(portfolio_view.get("summary"), dict) else {}
    assumptions = portfolio_view.get("assumptions") if isinstance(portfolio_view.get("assumptions"), dict) else {}
    risk_signals = portfolio_view.get("riskSignals") if isinstance(portfolio_view.get("riskSignals"), list) else []
    stock_exposure = portfolio_view.get("stockExposure") if isinstance(portfolio_view.get("stockExposure"), list) else []
    repeated_holdings = portfolio_view.get("repeatedHoldings") if isinstance(portfolio_view.get("repeatedHoldings"), list) else []
    return {
        "status": portfolio_view.get("status"),
        "assumptions": assumptions,
        "summary": summary,
        "riskSignals": [
            {
                "kind": item.get("kind"),
                "severity": item.get("severity"),
                "title": item.get("title"),
                "message": item.get("message"),
            }
            for item in risk_signals[:6]
            if isinstance(item, dict)
        ],
        "topExposure": [
            {
                "stockSymbol": item.get("stockSymbol"),
                "stockName": item.get("stockName"),
                "estimatedBasketExposurePercent": item.get("estimatedBasketExposurePercent"),
                "contributingFundCount": item.get("contributingFundCount"),
                "latestReportDate": item.get("latestReportDate"),
                "changePct": item.get("changePct"),
                "estimatedContribution": item.get("estimatedContribution"),
            }
            for item in stock_exposure[:10]
            if isinstance(item, dict)
        ],
        "repeatedHoldings": [
            {
                "stockSymbol": item.get("stockSymbol"),
                "stockName": item.get("stockName"),
                "estimatedBasketExposurePercent": item.get("estimatedBasketExposurePercent"),
                "contributingFundCount": item.get("contributingFundCount"),
            }
            for item in repeated_holdings[:6]
            if isinstance(item, dict)
        ],
    }


def _build_derived_facts(portfolio_view: dict[str, object]) -> str:
    summary = portfolio_view.get("summary") if isinstance(portfolio_view.get("summary"), dict) else {}
    active_fund_count = summary.get("activeFundCount")
    participating_fund_count = summary.get("participatingFundCount")
    repeated_holding_count = summary.get("repeatedHoldingCount")
    qdii_ratio = summary.get("qdiiFundRatio")
    top1_exposure = summary.get("top1ExposurePercent")
    stale_fund_count = summary.get("staleFundCount")
    return "\n".join(
        [
            f"当前激活基金数为 {active_fund_count}，已纳入观察池估算的基金数为 {participating_fund_count}。",
            f"重复持仓股票数为 {repeated_holding_count}。",
            f"观察池 Top1 单股估算权重为 {top1_exposure}%。" if isinstance(top1_exposure, (int, float)) else "观察池 Top1 单股估算权重未知。",
            f"QDII 基金占比约为 {round(float(qdii_ratio) * 100, 2)}%。" if isinstance(qdii_ratio, (int, float)) else "QDII 基金占比未知。",
            f"披露滞后基金数量为 {stale_fund_count}。",
            "这是基于最近披露重仓和等权观察池的解释，不是用户真实持仓结论。",
        ]
    )


def build_rule_fund_portfolio_analysis(portfolio_view: dict[str, object], *, focus: str, depth: str) -> dict[str, object]:
    summary = portfolio_view.get("summary") if isinstance(portfolio_view.get("summary"), dict) else {}
    risk_signals = portfolio_view.get("riskSignals") if isinstance(portfolio_view.get("riskSignals"), list) else []
    stock_exposure = portfolio_view.get("stockExposure") if isinstance(portfolio_view.get("stockExposure"), list) else []
    top_stock = stock_exposure[0] if stock_exposure else {}
    high_signal_count = sum(1 for item in risk_signals if isinstance(item, dict) and item.get("severity") == "high")
    warning_signal_count = sum(1 for item in risk_signals if isinstance(item, dict) and item.get("severity") == "warning")
    risk_level = "low"
    if high_signal_count > 0 or warning_signal_count >= 3:
        risk_level = "high"
    elif warning_signal_count > 0:
        risk_level = "medium"

    summary_text = "当前监控组合未触发显著结构性风险阈值，仍需关注披露滞后与单股波动。"
    top_stock_name = top_stock.get("stockName") or top_stock.get("stockSymbol")
    top_stock_exposure = top_stock.get("estimatedBasketExposurePercent")
    if isinstance(top_stock_exposure, (int, float)) and top_stock_name:
        summary_text = f"当前监控组合最显著的结构风险集中在 {top_stock_name}，估算权重约 {round(float(top_stock_exposure), 2)}%。"
    if high_signal_count > 0:
        summary_text = "当前监控组合已触发高优先级结构性风险提示，需优先关注重复持仓、集中度与披露滞后。"

    drivers = [
        str(item.get("title") or item.get("message"))
        for item in risk_signals[:3]
        if isinstance(item, dict) and (item.get("title") or item.get("message"))
    ]
    if not drivers:
        drivers = ["当前风险判断主要基于最近披露重仓和观察池等权聚合结果。"]

    watch_items = []
    if top_stock_name:
        watch_items.append(f"继续关注 {top_stock_name} 的单股波动对观察池估算收益的传导。")
    stale_count = summary.get("staleFundCount")
    if isinstance(stale_count, int) and stale_count > 0:
        watch_items.append(f"有 {stale_count} 只基金的披露期偏旧，后续应优先等待新报告期同步。")
    if not watch_items:
        watch_items.append("继续观察重复持仓与头部暴露是否进一步抬升。")

    limitations = [
        "当前解释仅基于最近披露重仓，不代表基金完整持仓。",
        "观察池默认按已同步基金等权估算，不代表你的真实持仓比例。",
        "本解读不构成投资建议。",
    ]

    return {
        "summary": _clip_text(summary_text, 140, "当前监控组合暂无可解释风险。"),
        "riskLevel": risk_level,
        "drivers": drivers[:4],
        "watchItems": watch_items[:4],
        "limitations": limitations,
        "confidence": 0.62 if risk_level == "low" else 0.68,
        "focus": focus,
        "depth": depth,
    }


def _normalize_analysis(value: dict[str, object], *, focus: str, depth: str) -> dict[str, object]:
    confidence = value.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.58
    confidence = min(max(float(confidence), 0.0), 1.0)
    return {
        "summary": _clip_text(value.get("summary"), 140, "当前监控组合存在一定结构性风险，需要结合披露边界谨慎解读。"),
        "riskLevel": _normalize_level(value.get("riskLevel")),
        "drivers": _normalize_text_list(value.get("drivers"), fallback=["风险主要来自披露持仓结构与集中度变化。"], item_max_chars=90, max_items=4),
        "watchItems": _normalize_text_list(value.get("watchItems"), fallback=["继续观察重复持仓、集中度和披露更新节奏。"], item_max_chars=90, max_items=4),
        "limitations": _normalize_text_list(
            value.get("limitations"),
            fallback=[
                "当前解释仅基于最近披露重仓。",
                "观察池默认按已同步基金等权估算，不代表你的真实持仓比例。",
                "本解读不构成投资建议。",
            ],
            item_max_chars=90,
            max_items=4,
        ),
        "confidence": round(confidence, 2),
        "focus": focus,
        "depth": depth,
    }


class FundPortfolioRiskAnalysisService:
    def __init__(self, settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return is_shared_llm_configured(self._settings)

    def analyze(self, *, portfolio_view: dict[str, object], focus: str, depth: str) -> FundPortfolioRiskAnalysisResult:
        if not self.is_configured():
            logger.warning("fund portfolio LLM analysis config incomplete or unsafe")
            return FundPortfolioRiskAnalysisResult(analysis=None, model_used=None, prompt_version="fund-portfolio-llm-v1", skip_reason="missing_model_config")

        snapshot_json = json.dumps(_build_analysis_input(portfolio_view), ensure_ascii=False, default=str)
        if len(snapshot_json) > LLM_MAX_INPUT_CHARS:
            snapshot_json = snapshot_json[:LLM_MAX_INPUT_CHARS]

        user_prompt = USER_PROMPT_TEMPLATE.format(
            focus=focus,
            depth=depth,
            derived_facts=_build_derived_facts(portfolio_view),
            snapshot_json=snapshot_json,
        )
        base_url = get_ai_base_url(self._settings)
        if base_url is None:
            return FundPortfolioRiskAnalysisResult(analysis=None, model_used=None, prompt_version="fund-portfolio-llm-v1", skip_reason="missing_model_config")
        url = f"{base_url.rstrip('/')}/chat/completions"
        timeout = get_ai_request_timeout_seconds(self._settings)
        retries = LLM_REQUEST_MAX_RETRIES
        last_latency_ms: int | None = None
        last_model: str | None = None
        last_skip_reason = "request_failed"
        attempts: list[dict[str, object]] = []
        for model in get_ai_model_candidates(self._settings):
            payload = build_openai_chat_payload(
                self._settings,
                model=model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=min(max(int(self._settings.ai_max_tokens), 512), 4096),
            )
            for attempt in range(retries + 1):
                started_at = time.monotonic()
                last_model = model
                try:
                    response = requests.post(
                        url,
                        headers=build_ai_headers(self._settings),
                        json=payload,
                        timeout=timeout,
                    )
                    if response.status_code >= 400:
                        raise requests.HTTPError(f"fund portfolio LLM upstream error {response.status_code}")
                    data = response.json()
                    last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                    choices = data.get("choices") if isinstance(data, dict) else None
                    usage = data.get("usage") if isinstance(data, dict) else None
                    if not isinstance(choices, list) or not choices:
                        last_skip_reason = "empty_choices"
                        attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status="missing_choices", status_code=response.status_code, usage=usage))
                        logger.warning("fund portfolio LLM response missing choices", extra={"model": model})
                        break
                    message = choices[0].get("message") if isinstance(choices[0], dict) else None
                    content = extract_chat_message_text(message)
                    parsed = _extract_json_object(content)
                    if parsed is None or _contains_forbidden_advice(parsed):
                        last_skip_reason = "invalid_model_output"
                        reason = "json_parse_failed" if parsed is None else "forbidden_advice_detected"
                        finish_reason = choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
                        status = "truncated_before_final_content" if parsed is None and finish_reason == "length" else "invalid_output"
                        attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status=status, status_code=response.status_code, finish_reason=finish_reason, message=message, usage=usage))
                        logger.warning("fund portfolio LLM response failed safety or JSON checks", extra={"attempt": attempt + 1, "reason": reason, "model": model})
                        if attempt < retries:
                            time.sleep(min(2 ** attempt, 8))
                            continue
                        break
                    attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status="completed", status_code=response.status_code, finish_reason=choices[0].get("finish_reason") if isinstance(choices[0], dict) else None, message=message, usage=usage))
                    return FundPortfolioRiskAnalysisResult(
                        analysis=_normalize_analysis(parsed, focus=focus, depth=depth),
                        model_used=model,
                        prompt_version="fund-portfolio-llm-v1",
                        attempted=True,
                        latency_ms=last_latency_ms,
                        attempts=attempts,
                    )
                except Exception as exc:
                    last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                    attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status="request_failed", error=exc))
                    if attempt >= retries:
                        logger.warning("fund portfolio LLM analysis request failed; trying fallback model if configured", extra={"model": model}, exc_info=exc)
                        break
                    time.sleep(min(2 ** attempt, 8))

        return FundPortfolioRiskAnalysisResult(analysis=None, model_used=last_model, prompt_version="fund-portfolio-llm-v1", attempted=True, skip_reason=last_skip_reason, latency_ms=last_latency_ms, attempts=attempts)
