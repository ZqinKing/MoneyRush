from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import requests

from app.services.llm_capabilities import LLM_MAX_INPUT_CHARS, LLM_REQUEST_MAX_RETRIES, LLM_REQUEST_TIMEOUT_SECONDS, build_ai_headers, build_openai_chat_payload, get_ai_base_url, get_ai_model_candidates, is_shared_llm_configured


logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
FORBIDDEN_ADVICE_TERMS = ("买入", "卖出", "持有", "推荐", "目标价", "加仓", "减仓", "抄底", "止盈", "止损")

SYSTEM_PROMPT = """你是 MoneyRush 的中文宏观市场解读助手。

你的任务是基于输入的 FRED 日度宏观快照，解释美债收益率、收益率曲线、VIX、美元指数和 S&P 500 对 QDII/美股估值环境的可能影响。

必须严格遵守：
1. 只能使用输入快照中的数据，不引入外部事实或新闻。
2. 不输出买入、卖出、持有、推荐、目标价、加仓、减仓、抄底、止盈、止损等投资建议或操作暗示。
3. 不预测确定性走势，不承诺收益，不给个股或基金操作建议。
4. 输出必须是 JSON 对象，不要输出 Markdown、解释过程或额外文本。
5. 语言使用中文，语气保守，强调这是宏观观察而非投资建议。"""

USER_PROMPT_TEMPLATE = """请基于以下 FRED 宏观快照生成宏观解读。

【分析焦点】{focus}
【详细程度】{depth}

【必须遵守的派生事实】
{derived_facts}

【宏观快照 JSON】
{snapshot_json}

请只输出一个 JSON 对象，字段如下：
{{
  "summary": "不超过120个中文字符的摘要",
  "impactLevel": "low | medium | high",
  "impactDirection": "positive | neutral | negative",
  "reasoning": {{
    "driver": "主要宏观驱动",
    "qdiiImpact": "对QDII/美股估值环境的保守解释",
    "timeHorizon": "短期 | 中期"
  }},
  "watch": {{
    "recommendation": "watch",
    "specific": "只描述需要观察的数据，不给操作建议"
  }},
  "risks": ["风险或限制1", "风险或限制2"],
  "keyLevels": {{"up": "上行观察位", "down": "下行观察位"}},
  "confidence": 0.0,
  "focus": "{focus}",
  "depth": "{depth}"
}}"""


@dataclass(slots=True)
class MacroAnalysisResult:
    analysis: dict[str, object] | None
    model_used: str | None
    prompt_version: str
    attempted: bool = False
    skip_reason: str | None = None
    latency_ms: int | None = None


def _strip_reasoning_text(value: object) -> str | None:
    if value is None:
        return None
    text = _THINK_BLOCK_RE.sub("", str(value)).strip()
    return text or None


def _extract_json_object(value: object) -> dict[str, object] | None:
    text = _strip_reasoning_text(value)
    if not text:
        return None
    candidates = [text]
    match = _JSON_BLOCK_RE.search(text)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def _contains_forbidden_advice(value: object) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, dict) else str(value or "")
    return any(term in text for term in FORBIDDEN_ADVICE_TERMS)


def _number_from_path(value: dict[str, object], *path: str) -> float | None:
    current: object = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return float(current) if isinstance(current, (int, float)) and not isinstance(current, bool) else None


def _build_derived_facts(snapshot: dict[str, object]) -> str:
    y10 = _number_from_path(snapshot, "yields", "y10", "value")
    y10_change_5d = _number_from_path(snapshot, "yields", "y10", "changeD5Bp")
    spread_bp = _number_from_path(snapshot, "yields", "spread10Y2YBp")
    curve_state = "10Y-2Y利差数据缺失，不能判断倒挂"
    if spread_bp is not None:
        curve_state = f"10Y-2Y利差为{spread_bp:.1f}bp，{'未倒挂' if spread_bp >= 0 else '处于倒挂'}。不得把正利差描述为倒挂。"
    y10_state = "10Y收益率数据缺失"
    if y10 is not None:
        y10_state = f"10Y收益率为{y10:.2f}%"
        if y10_change_5d is not None:
            y10_state = f"{y10_state}，5日变化{y10_change_5d:+.1f}bp"
    return "\n".join((curve_state, y10_state))


def _violates_snapshot_facts(analysis: dict[str, object], snapshot: dict[str, object]) -> bool:
    text = json.dumps(analysis, ensure_ascii=False, default=str)
    spread_bp = _number_from_path(snapshot, "yields", "spread10Y2YBp")
    if spread_bp is not None and spread_bp >= 0 and "倒挂" in text and "未倒挂" not in text:
        return True
    return False


def _normalize_direction(value: object) -> str:
    text = str(value or "neutral").strip().lower()
    return text if text in {"positive", "neutral", "negative"} else "neutral"


def _normalize_level(value: object) -> str:
    text = str(value or "low").strip().lower()
    return text if text in {"low", "medium", "high"} else "low"


def _clip_text(value: object, max_chars: int, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:max_chars]


def _normalize_analysis(value: dict[str, object], *, focus: str, depth: str) -> dict[str, object]:
    confidence = value.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.55
    confidence = min(max(float(confidence), 0.0), 1.0)

    reasoning = value.get("reasoning") if isinstance(value.get("reasoning"), dict) else {}
    watch = value.get("watch") if isinstance(value.get("watch"), dict) else {}
    key_levels = value.get("keyLevels") if isinstance(value.get("keyLevels"), dict) else {}
    risks = value.get("risks") if isinstance(value.get("risks"), list) else []
    normalized_risks = [_clip_text(item, 80, "宏观数据存在滞后。") for item in risks[:4]]
    if not normalized_risks:
        normalized_risks = ["FRED 为日度数据，非实时行情。", "本解读不构成投资建议。"]

    return {
        "summary": _clip_text(value.get("summary"), 140, "宏观环境整体偏中性，需继续观察美债收益率与风险偏好变化。"),
        "impactLevel": _normalize_level(value.get("impactLevel")),
        "impactDirection": _normalize_direction(value.get("impactDirection")),
        "reasoning": {
            "driver": _clip_text(reasoning.get("driver"), 180, "基于 FRED 宏观快照生成。"),
            "qdiiImpact": _clip_text(reasoning.get("qdiiImpact"), 220, "对 QDII/美股估值环境的影响需结合收益率和风险偏好观察。"),
            "timeHorizon": _clip_text(reasoning.get("timeHorizon"), 20, "短期"),
        },
        "watch": {
            "recommendation": "watch",
            "specific": _clip_text(watch.get("specific"), 220, "继续观察10Y收益率、VIX、美元指数和S&P 500的同步变化。"),
        },
        "risks": normalized_risks,
        "keyLevels": {
            "up": _clip_text(key_levels.get("up"), 80, "观察10Y收益率继续上行。"),
            "down": _clip_text(key_levels.get("down"), 80, "观察10Y收益率回落。"),
        },
        "confidence": round(confidence, 2),
        "focus": focus,
        "depth": depth,
    }


class MacroAnalysisService:
    def __init__(self, settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return is_shared_llm_configured(self._settings)

    def analyze(self, *, snapshot: dict[str, object], focus: str, depth: str) -> MacroAnalysisResult:
        if not self.is_configured():
            logger.warning("macro LLM analysis config incomplete or unsafe")
            return MacroAnalysisResult(analysis=None, model_used=None, prompt_version="macro-llm-v1", skip_reason="missing_model_config")

        snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
        if len(snapshot_json) > LLM_MAX_INPUT_CHARS:
            snapshot_json = snapshot_json[:LLM_MAX_INPUT_CHARS]
        user_prompt = USER_PROMPT_TEMPLATE.format(
            focus=focus,
            depth=depth,
            derived_facts=_build_derived_facts(snapshot),
            snapshot_json=snapshot_json,
        )
        base_url = get_ai_base_url(self._settings)
        if base_url is None:
            return MacroAnalysisResult(analysis=None, model_used=None, prompt_version="macro-llm-v1", skip_reason="missing_model_config")
        url = f"{base_url.rstrip('/')}/chat/completions"
        timeout = LLM_REQUEST_TIMEOUT_SECONDS
        retries = LLM_REQUEST_MAX_RETRIES
        last_latency_ms: int | None = None
        last_model: str | None = None
        last_skip_reason = "request_failed"
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
                        raise requests.HTTPError(f"macro LLM upstream error {response.status_code}")
                    data = response.json()
                    last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                    choices = data.get("choices") if isinstance(data, dict) else None
                    if not isinstance(choices, list) or not choices:
                        last_skip_reason = "empty_choices"
                        logger.warning("macro LLM response missing choices", extra={"model": model})
                        break
                    message = choices[0].get("message") if isinstance(choices[0], dict) else None
                    content = message.get("content") if isinstance(message, dict) else None
                    parsed = _extract_json_object(content)
                    if parsed is None or _contains_forbidden_advice(parsed) or _violates_snapshot_facts(parsed, snapshot):
                        last_skip_reason = "invalid_model_output"
                        logger.warning("macro LLM response failed safety or JSON checks", extra={"model": model})
                        break
                    return MacroAnalysisResult(
                        analysis=_normalize_analysis(parsed, focus=focus, depth=depth),
                        model_used=model,
                        prompt_version="macro-llm-v1",
                        attempted=True,
                        latency_ms=last_latency_ms,
                    )
                except Exception as exc:
                    last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                    if attempt >= retries:
                        logger.warning("macro LLM analysis request failed; trying fallback model if configured", extra={"model": model}, exc_info=exc)
                        break
                    time.sleep(min(2 ** attempt, 8))

        return MacroAnalysisResult(analysis=None, model_used=last_model, prompt_version="macro-llm-v1", attempted=True, skip_reason=last_skip_reason, latency_ms=last_latency_ms)
