from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests

from shared.llm_protocol import (
    LLM_REASONING_BUDGET_TOKENS,
    LLM_REQUEST_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT_SECONDS,
    build_ai_headers as _build_ai_headers,
    build_llm_attempt_meta,
    build_llm_request,
    build_openai_chat_payload,
    bounded_positive_int as _bounded_positive_int,
    clean_optional_text as _clean_optional_text,
    coerce_message_text as _coerce_message_text,
    extract_chat_message_text,
    get_ai_base_url,
    get_ai_model,
    get_ai_model_candidates,
    get_ai_reasoning_budget_tokens,
    get_ai_request_timeout_seconds,
    is_ai_configured,
    is_safe_ai_base_url,
    parse_llm_response,
)

__all__ = [
    "CONTENT_SUMMARY_MAX_NEWS_AGE_SECONDS",
    "CONTENT_SUMMARY_PROMPT_VERSION",
    "LLM_REQUEST_MAX_RETRIES",
    "AiSummaryClient",
    "AiSummaryResult",
    "build_llm_attempt_meta",
    "build_llm_request",
    "build_openai_chat_payload",
    "extract_chat_message_text",
    "get_ai_base_url",
    "get_ai_model",
    "get_ai_model_candidates",
    "get_ai_request_timeout_seconds",
    "is_ai_configured",
    "normalize_ai_text",
    "parse_llm_response",
]


logger = logging.getLogger(__name__)
LLM_DEFAULT_TEMPERATURE = 0.0
CONTENT_SUMMARY_MAX_INPUT_CHARS = 131072
CONTENT_SUMMARY_MIN_CONTENT_LENGTH = 180
CONTENT_SUMMARY_MAX_NEWS_AGE_SECONDS = 1800
CONTENT_SUMMARY_PROMPT_VERSION = "v1"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_QUOTED_SUMMARY_RE = re.compile(r'[“"]([^“”"\n]{20,240})[”"]')
_SUMMARY_MARKERS = ("我来写摘要：", "摘要：", "最终摘要：", "输出摘要：", "输出：")
_COMMENTARY_PREFIXES = ("这个", "字数", "符合", "让我", "检查", "要求", "说明")
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[，。、“”‘’：:；;,.!?！？（）()【】\[\]<>《》\-—_+/\\|]")
_GENERIC_NEWS_MARKERS = (
    "数据看盘",
    "财经晚报",
    "资金流入",
    "资金流出",
    "资金榜",
    "龙虎榜",
    "涨停复盘",
    "板块",
    "ETF",
    "指数",
    "市场综述",
    "收盘播报",
    "午盘播报",
    "复盘",
)


SYSTEM_PROMPT = """你是一个中文财经资讯摘要助手。

你的任务是基于提供的新闻原文，为股票行情资讯流生成简短、事实导向、可追溯的中文摘要。

必须遵守以下规则：
1. 只能使用输入中的标题、正文和来源信息，不能引入外部知识。
2. 保留公司名、人名、日期、时间、金额、百分比、价格、代码等关键信息的原始含义，不要擅自改写数字。
3. 不要猜测未出现的信息；若正文未明确说明因果、影响或结论，不要补充推断。
4. 不要把“提及某公司”总结成“该公司受益/受损”，除非正文明确说明。
5. 输出应简洁、适合资讯卡片阅读，不超过 2 句话。
6. 不要使用标题党、感叹语或投资建议。
7. 如果正文不足以生成比原摘要更有价值的内容，输出应尽量保守。"""


USER_PROMPT_TEMPLATE = """请基于以下内容生成一段中文财经资讯摘要。

【标题】
{title}

【来源】
{article_source}

【原始摘要】
{raw_summary}

【正文】
{content}

输出要求：
1. 用中文输出。
2. 只输出摘要正文，不要输出解释、标签或前缀。
3. 摘要长度控制在 60~120 个中文字符，最多 2 句话。
4. 优先总结“发生了什么”和“与该资讯主体最相关的事实”。
5. 若正文主要是市场背景、ETF说明或泛行业描述，则摘要应忠实反映这一点，不要强行拔高到个股结论。"""


def _build_prompts(*, prompt_version: str, title: str, article_source: str | None, raw_summary: str | None, content: str) -> tuple[str, str]:
    normalized_version = (prompt_version or "v1").strip().lower()
    if normalized_version != "v1":
        logger.warning("unsupported ai summary prompt version; fallback to v1", extra={"promptVersion": prompt_version})
    return (
        SYSTEM_PROMPT,
        USER_PROMPT_TEMPLATE.format(
            title=title,
            article_source=article_source or "--",
            raw_summary=raw_summary or "--",
            content=content,
        ),
    )


def _normalize_summary_content(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    stripped = _THINK_BLOCK_RE.sub("", text).strip()
    if stripped and "<think>" not in stripped.lower():
        return stripped

    extracted = _extract_summary_from_reasoning_text(text)
    if extracted:
        return extracted

    return None


def normalize_ai_text(value: object) -> str | None:
    return _normalize_summary_content(value)


def _extract_summary_from_reasoning_text(text: str) -> str | None:
    working = text.strip()
    for marker in _SUMMARY_MARKERS:
        index = working.rfind(marker)
        if index >= 0:
            working = working[index + len(marker):].strip()
            break

    for candidate in _QUOTED_SUMMARY_RE.findall(working):
        normalized = candidate.strip()
        if _looks_like_summary(normalized):
            return normalized

    for raw_line in working.splitlines():
        normalized = raw_line.strip().strip("“”\"' ")
        normalized = re.sub(r"^[：:\-–—\s]+", "", normalized).strip().strip("“”\"' ")
        if not normalized:
            continue
        if any(normalized.startswith(prefix) for prefix in _COMMENTARY_PREFIXES):
            break
        if _looks_like_summary(normalized):
            return normalized

    return None


def _looks_like_summary(text: str) -> bool:
    if len(text) < 20:
        return False
    if "<think>" in text.lower():
        return False
    return True


def _truncate_content_for_budget(
    *,
    max_input_chars: int,
    title: str,
    article_source: str | None,
    raw_summary: str | None,
    content: str,
) -> str:
    if max_input_chars <= 0:
        return content

    prompt_without_content = USER_PROMPT_TEMPLATE.format(
        title=title,
        article_source=article_source or "--",
        raw_summary=raw_summary or "--",
        content="",
    )
    reserved_chars = len(SYSTEM_PROMPT) + len(prompt_without_content)
    available_chars = max_input_chars - reserved_chars
    if available_chars <= 0:
        return ""
    if len(content) <= available_chars:
        return content
    return content[:available_chars]


@dataclass(slots=True)
class AiSummaryResult:
    summary: str | None
    attempted: bool = False
    skip_reason: str | None = None
    model_used: str | None = None
    prompt_version: str = "v1"
    latency_ms: int | None = None
    attempts: list[dict[str, object]] | None = None


def _normalize_for_overlap(value: str | None) -> str:
    if not value:
        return ""
    collapsed = _WHITESPACE_RE.sub("", value)
    return _PUNCT_RE.sub("", collapsed).lower()


def _unique_tail_length(*, content: str, raw_summary: str | None) -> int:
    normalized_content = _normalize_for_overlap(content)
    normalized_summary = _normalize_for_overlap(raw_summary)
    if not normalized_content:
        return 0
    if not normalized_summary:
        return len(normalized_content)
    remaining = normalized_content
    for fragment in (normalized_summary[i : i + 24] for i in range(0, max(len(normalized_summary) - 23, 1), 12)):
        if len(fragment) < 12:
            continue
        remaining = remaining.replace(fragment, "")
    return len(remaining)


def _is_generic_news(*, title: str, raw_summary: str | None, content: str) -> bool:
    sample = "\n".join(part for part in (title, raw_summary or "", content[:240]) if part).lower()
    return any(marker.lower() in sample for marker in _GENERIC_NEWS_MARKERS)


def _get_skip_reason(*, min_content_length: int, title: str, raw_summary: str | None, content: str) -> str | None:
    normalized_content = content.strip()
    if len(normalized_content) < max(min_content_length, 0):
        return "content_too_short"
    comparable_content = _normalize_for_overlap(normalized_content)
    comparable_summary = _normalize_for_overlap(raw_summary)
    if comparable_summary and comparable_content and comparable_summary == comparable_content:
        return "content_equivalent_to_summary"
    unique_tail_length = _unique_tail_length(content=normalized_content, raw_summary=raw_summary)
    if comparable_summary and unique_tail_length < 80:
        return "content_increment_too_small"
    if _is_generic_news(title=title, raw_summary=raw_summary, content=normalized_content):
        return "content_too_generic"
    return None


class AiSummaryClient:
    def __init__(self, settings) -> None:
        self._settings = settings

    def summarize(self, *, title: str, article_source: str | None, raw_summary: str | None, content: str) -> AiSummaryResult:
        prompt_version = CONTENT_SUMMARY_PROMPT_VERSION
        if not get_ai_base_url(self._settings) or not get_ai_model(self._settings):
            logger.warning("content ai summary config incomplete")
            return AiSummaryResult(summary=None, skip_reason="missing_model_config", prompt_version=prompt_version)
        if not is_ai_configured(self._settings):
            logger.warning("content ai summary base url must be https with host")
            return AiSummaryResult(summary=None, skip_reason="invalid_base_url", prompt_version=prompt_version)
        normalized_content = content.strip()
        skip_reason = _get_skip_reason(
            min_content_length=CONTENT_SUMMARY_MIN_CONTENT_LENGTH,
            title=title,
            raw_summary=raw_summary,
            content=normalized_content,
        )
        if skip_reason is not None:
            return AiSummaryResult(summary=None, skip_reason=skip_reason, prompt_version=prompt_version)

        prompt_content = _truncate_content_for_budget(
            max_input_chars=CONTENT_SUMMARY_MAX_INPUT_CHARS,
            title=title,
            article_source=article_source,
            raw_summary=raw_summary,
            content=normalized_content,
        )
        if len(prompt_content) < CONTENT_SUMMARY_MIN_CONTENT_LENGTH:
            return AiSummaryResult(summary=None, skip_reason="content_too_short", prompt_version=prompt_version)

        system_prompt, user_prompt = _build_prompts(
            prompt_version=CONTENT_SUMMARY_PROMPT_VERSION,
            title=title,
            article_source=article_source,
            raw_summary=raw_summary,
            content=prompt_content,
        )

        if get_ai_base_url(self._settings) is None:
            return AiSummaryResult(summary=None, skip_reason="missing_model_config", prompt_version=prompt_version)
        timeout = get_ai_request_timeout_seconds(self._settings)
        retries = LLM_REQUEST_MAX_RETRIES
        last_latency_ms: int | None = None
        last_model: str | None = None
        attempts: list[dict[str, object]] = []
        for model in get_ai_model_candidates(self._settings):
            llm_request = build_llm_request(
                self._settings,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            for attempt in range(retries + 1):
                started_at = time.monotonic()
                last_model = model
                try:
                    response = requests.post(
                        llm_request.url,
                        headers=llm_request.headers,
                        json=llm_request.payload,
                        timeout=timeout,
                    )
                    if response.status_code >= 400:
                        if response.status_code in {408, 429} or response.status_code >= 500:
                            raise requests.HTTPError(f"ai summary upstream error {response.status_code}")
                        response.raise_for_status()
                    data = response.json()
                    last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                    parsed_response = parse_llm_response(data, protocol=llm_request.protocol)
                    if parsed_response.text is None:
                        attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status="missing_choices", status_code=response.status_code, usage=parsed_response.usage, provider=llm_request.provider, protocol=llm_request.protocol))
                        logger.warning("ai summary response missing choices", extra={"model": model})
                        break
                    summary_text = _normalize_summary_content(parsed_response.text)
                    if summary_text is None:
                        status = "truncated_before_final_content" if parsed_response.finish_reason in {"length", "incomplete"} else "missing_final_content"
                        attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status=status, status_code=response.status_code, finish_reason=parsed_response.finish_reason, message=parsed_response.message, usage=parsed_response.usage, provider=llm_request.provider, protocol=llm_request.protocol))
                        logger.warning("ai summary response missing usable final content", extra={"model": model, "finishReason": parsed_response.finish_reason})
                        break
                    attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status="completed", status_code=response.status_code, finish_reason=parsed_response.finish_reason, message=parsed_response.message, usage=parsed_response.usage, provider=llm_request.provider, protocol=llm_request.protocol))
                    return AiSummaryResult(
                        summary=summary_text,
                        attempted=True,
                        model_used=model,
                        prompt_version=prompt_version,
                        latency_ms=last_latency_ms,
                        attempts=attempts,
                    )
                except Exception as exc:
                    last_latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
                    attempts.append(build_llm_attempt_meta(model=model, attempt=attempt + 1, latency_ms=last_latency_ms, status="request_failed", error=exc))
                    if attempt >= retries:
                        logger.warning("ai summary request failed; trying fallback model if configured", extra={"model": model}, exc_info=exc)
                        break
                    time.sleep(min(2 ** attempt, 8))
        return AiSummaryResult(
            summary=None,
            attempted=True,
            model_used=last_model,
            prompt_version=prompt_version,
            latency_ms=last_latency_ms,
            attempts=attempts,
        )
