from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


LLM_DEFAULT_TEMPERATURE = 0.0
LLM_REASONING_BUDGET_TOKENS = 2048
LLM_REQUEST_TIMEOUT_SECONDS = 45
LLM_REQUEST_MAX_RETRIES = 2
LLM_MAX_INPUT_CHARS = 131072


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


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def get_ai_base_url(settings) -> str | None:
    return _clean_optional_text(getattr(settings, "ai_base_url", None))


def get_ai_api_key(settings) -> str | None:
    return _clean_optional_text(getattr(settings, "ai_api_key", None))


def get_ai_model(settings) -> str | None:
    return _clean_optional_text(getattr(settings, "ai_model", None))


def get_ai_model_candidates(settings) -> list[str]:
    candidates = [get_ai_model(settings), _clean_optional_text(getattr(settings, "ai_fallback_model", None))]
    models: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in models:
            models.append(candidate)
    return models


def is_shared_llm_configured(settings) -> bool:
    base_url = get_ai_base_url(settings)
    return bool(base_url and get_ai_model(settings) and is_safe_ai_base_url(base_url))


def build_ai_headers(settings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = get_ai_api_key(settings)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _bounded_positive_int(value: object, *, default: int, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(number, minimum)


def build_openai_chat_payload(settings, *, model: str, system_prompt: str, user_prompt: str, max_tokens: int | None = None) -> dict[str, object]:
    token_budget = max_tokens if max_tokens is not None else getattr(settings, "ai_max_tokens", 8192)
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": LLM_DEFAULT_TEMPERATURE,
        "max_tokens": _bounded_positive_int(token_budget, default=8192),
        "context_length": _bounded_positive_int(getattr(settings, "ai_context_length", 131072), default=131072),
        "reasoning_split": True,
        "stream": False,
    }
    if getattr(settings, "ai_thinking_enabled", False):
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": LLM_REASONING_BUDGET_TOKENS,
        }
    return payload


def get_llm_feature_capabilities(settings) -> dict[str, object]:
    shared_configured = is_shared_llm_configured(settings)
    content_summary_enabled = shared_configured
    anomaly_reason_enabled = shared_configured
    fund_portfolio_risk_enabled = shared_configured
    macro_analysis_available = (
        shared_configured
        and settings.macro_monitor_enabled
        and bool(settings.fred_api_key)
    )

    return {
        "enabled": content_summary_enabled or anomaly_reason_enabled or fund_portfolio_risk_enabled or macro_analysis_available,
        "reason": None if (content_summary_enabled or anomaly_reason_enabled or fund_portfolio_risk_enabled or macro_analysis_available) else "config_disabled",
        "features": {
            "contentSummary": content_summary_enabled,
            "anomalyReason": anomaly_reason_enabled,
            "fundPortfolioRiskAnalysis": fund_portfolio_risk_enabled,
            "macroAnalysis": macro_analysis_available,
        },
    }
