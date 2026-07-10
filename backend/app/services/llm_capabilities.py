from __future__ import annotations

from shared.llm_protocol import (
    LLM_MAX_INPUT_CHARS,
    LLM_REQUEST_MAX_RETRIES,
    build_ai_headers,
    build_llm_attempt_meta,
    build_llm_request,
    build_openai_chat_payload,
    bounded_positive_int as _bounded_positive_int,
    clean_optional_text as _clean_optional_text,
    coerce_message_text as coerce_chat_message_text,
    extract_chat_message_text,
    get_ai_base_url,
    get_ai_max_output_tokens,
    get_ai_model,
    get_ai_model_candidates,
    get_ai_reasoning_budget_tokens,
    get_ai_request_timeout_seconds,
    is_ai_configured as is_shared_llm_configured,
    is_safe_ai_base_url,
    parse_llm_response,
)

__all__ = [
    "LLM_MAX_INPUT_CHARS",
    "LLM_REQUEST_MAX_RETRIES",
    "build_ai_headers",
    "build_llm_attempt_meta",
    "build_llm_request",
    "build_openai_chat_payload",
    "coerce_chat_message_text",
    "extract_chat_message_text",
    "get_ai_base_url",
    "get_ai_max_output_tokens",
    "get_ai_model",
    "get_ai_model_candidates",
    "get_ai_reasoning_budget_tokens",
    "get_ai_request_timeout_seconds",
    "get_llm_feature_capabilities",
    "is_safe_ai_base_url",
    "is_shared_llm_configured",
    "parse_llm_response",
]


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
