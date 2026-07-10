from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse


LLM_DEFAULT_TEMPERATURE = 0.0
LLM_REASONING_BUDGET_TOKENS = 2048
LLM_REQUEST_TIMEOUT_SECONDS = 45
LLM_REQUEST_MAX_RETRIES = 2
LLM_MAX_INPUT_CHARS = 131072

OPENAI_CHAT_PROTOCOL = "openai-chat"
OPENAI_RESPONSES_PROTOCOL = "openai-responses"
ANTHROPIC_MESSAGES_PROTOCOL = "anthropic-messages"
SUPPORTED_PROTOCOLS = {OPENAI_CHAT_PROTOCOL, OPENAI_RESPONSES_PROTOCOL, ANTHROPIC_MESSAGES_PROTOCOL}
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
__all__ = [
    "ANTHROPIC_MESSAGES_PROTOCOL",
    "LLM_MAX_INPUT_CHARS",
    "LLM_REASONING_BUDGET_TOKENS",
    "LLM_REQUEST_MAX_RETRIES",
    "LLM_REQUEST_TIMEOUT_SECONDS",
    "OPENAI_CHAT_PROTOCOL",
    "OPENAI_RESPONSES_PROTOCOL",
    "LlmRequest",
    "ParsedLlmResponse",
    "bounded_positive_int",
    "build_ai_headers",
    "build_llm_attempt_meta",
    "build_llm_request",
    "build_openai_chat_payload",
    "clean_optional_text",
    "coerce_message_text",
    "extract_chat_message_text",
    "get_ai_base_url",
    "get_ai_max_output_tokens",
    "get_ai_model",
    "get_ai_model_candidates",
    "get_ai_reasoning_budget_tokens",
    "get_ai_request_timeout_seconds",
    "is_ai_configured",
    "is_safe_ai_base_url",
    "parse_llm_response",
]


@dataclass(frozen=True, slots=True)
class LlmRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, object]
    provider: str
    protocol: str


@dataclass(frozen=True, slots=True)
class ParsedLlmResponse:
    text: str | None
    finish_reason: str | None
    usage: object
    message: dict[str, object] | None
    reasoning_content_length: int
    raw: dict[str, object]


def clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def bounded_positive_int(value: object, *, default: int, minimum: int = 1) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(value, minimum)
    if isinstance(value, float):
        return max(int(value), minimum)
    if isinstance(value, str):
        try:
            return max(int(value.strip()), minimum)
        except ValueError:
            return default
    return default


def is_safe_ai_base_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    hostname = parsed.hostname.strip().lower()
    if hostname == "localhost":
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified)


def get_ai_base_url(settings: object) -> str | None:
    return clean_optional_text(getattr(settings, "ai_base_url", None))


def get_ai_api_key(settings: object) -> str | None:
    return clean_optional_text(getattr(settings, "ai_api_key", None))


def get_ai_provider(settings: object) -> str:
    return clean_optional_text(getattr(settings, "ai_provider", None)) or "openai"


def get_ai_protocol(settings: object) -> str:
    protocol = (clean_optional_text(getattr(settings, "ai_protocol", None)) or OPENAI_CHAT_PROTOCOL).lower()
    return protocol if protocol in SUPPORTED_PROTOCOLS else OPENAI_CHAT_PROTOCOL


def get_ai_model(settings: object) -> str | None:
    return clean_optional_text(getattr(settings, "ai_model", None))


def get_ai_model_candidates(settings: object) -> list[str]:
    candidates = [get_ai_model(settings), clean_optional_text(getattr(settings, "ai_fallback_model", None))]
    models: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in models:
            models.append(candidate)
    return models


def get_ai_request_timeout_seconds(settings: object) -> int:
    return bounded_positive_int(getattr(settings, "ai_request_timeout_seconds", LLM_REQUEST_TIMEOUT_SECONDS), default=LLM_REQUEST_TIMEOUT_SECONDS)


def get_ai_max_output_tokens(settings: object, override: int | None = None) -> int:
    if override is not None:
        return bounded_positive_int(override, default=8192)
    configured = getattr(settings, "ai_max_output_tokens", None)
    if configured is None:
        configured = getattr(settings, "ai_max_tokens", 8192)
    return bounded_positive_int(configured, default=8192)


def get_ai_reasoning_enabled(settings: object) -> bool:
    return bool(getattr(settings, "ai_reasoning_enabled", False) or getattr(settings, "ai_thinking_enabled", False))


def get_ai_reasoning_effort(settings: object) -> str:
    effort = (clean_optional_text(getattr(settings, "ai_reasoning_effort", None)) or "low").lower()
    return effort if effort in REASONING_EFFORTS else "low"


def get_ai_reasoning_budget_tokens(settings: object) -> int:
    return bounded_positive_int(getattr(settings, "ai_reasoning_budget_tokens", LLM_REASONING_BUDGET_TOKENS), default=LLM_REASONING_BUDGET_TOKENS, minimum=1024)


def get_ai_anthropic_version(settings: object) -> str:
    return clean_optional_text(getattr(settings, "ai_anthropic_version", None)) or "2023-06-01"


def is_ai_configured(settings: object) -> bool:
    base_url = get_ai_base_url(settings)
    return bool(base_url and get_ai_model(settings) and is_safe_ai_base_url(base_url))


def build_ai_headers(settings: object, *, protocol: str | None = None) -> dict[str, str]:
    resolved_protocol = protocol or get_ai_protocol(settings)
    headers = {"Content-Type": "application/json"}
    api_key = get_ai_api_key(settings)
    if resolved_protocol == ANTHROPIC_MESSAGES_PROTOCOL:
        headers["anthropic-version"] = get_ai_anthropic_version(settings)
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_llm_request(settings: object, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> LlmRequest:
    base_url = get_ai_base_url(settings)
    if base_url is None:
        raise ValueError("missing AI base URL")
    provider = get_ai_provider(settings)
    protocol = get_ai_protocol(settings)
    output_tokens = get_ai_max_output_tokens(settings, max_tokens)
    reasoning_enabled = get_ai_reasoning_enabled(settings)
    temperature = LLM_DEFAULT_TEMPERATURE

    if protocol == OPENAI_RESPONSES_PROTOCOL:
        payload: dict[str, object] = {
            "model": model,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": output_tokens,
            "temperature": temperature,
        }
        if reasoning_enabled:
            payload["reasoning"] = {"effort": get_ai_reasoning_effort(settings)}
        return LlmRequest(
            url=f"{base_url.rstrip('/')}/responses",
            headers=build_ai_headers(settings, protocol=protocol),
            payload=payload,
            provider=provider,
            protocol=protocol,
        )

    if protocol == ANTHROPIC_MESSAGES_PROTOCOL:
        payload = {
            "model": model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": output_tokens,
            "stream": False,
        }
        if reasoning_enabled:
            budget = get_ai_reasoning_budget_tokens(settings)
            if output_tokens <= budget:
                output_tokens = budget + 1
                payload["max_tokens"] = output_tokens
            payload["thinking"] = {"type": "enabled", "budget_tokens": min(budget, output_tokens - 1)}
        else:
            payload["temperature"] = temperature
        return LlmRequest(
            url=f"{base_url.rstrip('/')}/messages",
            headers=build_ai_headers(settings, protocol=protocol),
            payload=payload,
            provider=provider,
            protocol=protocol,
        )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": output_tokens,
        "stream": False,
    }
    if reasoning_enabled:
        payload["reasoning_effort"] = get_ai_reasoning_effort(settings)
    return LlmRequest(
        url=f"{base_url.rstrip('/')}/chat/completions",
        headers=build_ai_headers(settings, protocol=protocol),
        payload=payload,
        provider=provider,
        protocol=protocol,
    )


def build_openai_chat_payload(settings: object, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> dict[str, object]:
    return build_llm_request(settings, model=model, system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens).payload


def coerce_message_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = str(value.get("text") or value.get("content") or value.get("value") or "")
    elif isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                part = item.get("text") or item.get("content") or item.get("value")
                if isinstance(part, str) and part.strip():
                    parts.append(part)
        text = "\n".join(parts)
    else:
        text = str(value)
    normalized = text.strip()
    return normalized or None


def extract_chat_message_text(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    for key in ("content", "output_text", "final", "answer", "text"):
        text = coerce_message_text(message.get(key))
        if text:
            return text
    return None


def parse_llm_response(data: object, *, protocol: str) -> ParsedLlmResponse:
    raw = cast(dict[str, object], data) if isinstance(data, dict) else {}
    if protocol == OPENAI_RESPONSES_PROTOCOL:
        text = coerce_message_text(raw.get("output_text"))
        reasoning_length = 0
        response_message: dict[str, object] | None = None
        if text is None:
            parts: list[str] = []
            output = raw.get("output")
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "reasoning":
                        reasoning_length += len(coerce_message_text(item.get("summary")) or "")
                        continue
                    if item.get("type") == "message":
                        response_message = cast(dict[str, object], item)
                    content = item.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            block_text = coerce_message_text(block.get("text"))
                            if block_text:
                                parts.append(block_text)
            text = "\n".join(parts).strip() or None
        finish_reason = clean_optional_text(raw.get("status"))
        return ParsedLlmResponse(text=text, finish_reason=finish_reason, usage=raw.get("usage"), message=response_message, reasoning_content_length=reasoning_length, raw=raw)

    if protocol == ANTHROPIC_MESSAGES_PROTOCOL:
        parts = []
        reasoning_length = 0
        content = raw.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text = coerce_message_text(block.get("text"))
                    if text:
                        parts.append(text)
                elif block_type == "thinking":
                    reasoning_length += len(coerce_message_text(block.get("thinking")) or "")
        content_text = "\n".join(parts).strip()
        anthropic_message: dict[str, object] = {"content": content_text, "reasoning_content": ""}
        return ParsedLlmResponse(text=content_text or None, finish_reason=clean_optional_text(raw.get("stop_reason")), usage=raw.get("usage"), message=anthropic_message, reasoning_content_length=reasoning_length, raw=raw)

    choices = raw.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    message_obj = choice.get("message") if isinstance(choice, dict) else None
    chat_message = cast(dict[str, object], message_obj) if isinstance(message_obj, dict) else None
    reasoning = coerce_message_text(chat_message.get("reasoning_content")) if isinstance(chat_message, dict) else None
    finish_reason = clean_optional_text(choice.get("finish_reason")) if isinstance(choice, dict) else None
    return ParsedLlmResponse(text=extract_chat_message_text(chat_message), finish_reason=finish_reason, usage=raw.get("usage"), message=chat_message, reasoning_content_length=len(reasoning or ""), raw=raw)


def build_llm_attempt_meta(
    *,
    model: str,
    attempt: int,
    latency_ms: int,
    status: str,
    status_code: int | None = None,
    finish_reason: object = None,
    message: object = None,
    usage: object = None,
    error: object = None,
    provider: str | None = None,
    protocol: str | None = None,
) -> dict[str, object]:
    meta: dict[str, object] = {
        "model": model,
        "attempt": attempt,
        "latencyMs": latency_ms,
        "status": status,
    }
    if provider is not None:
        meta["provider"] = provider
    if protocol is not None:
        meta["protocol"] = protocol
    if status_code is not None:
        meta["statusCode"] = status_code
    if finish_reason is not None:
        meta["finishReason"] = str(finish_reason)
    if isinstance(message, dict):
        content = coerce_message_text(message.get("content"))
        reasoning = coerce_message_text(message.get("reasoning_content"))
        meta["contentLength"] = len(content or "")
        meta["reasoningContentLength"] = len(reasoning or "")
    if isinstance(usage, dict):
        meta["usage"] = usage
    if error is not None:
        meta["errorType"] = type(error).__name__
    return meta
