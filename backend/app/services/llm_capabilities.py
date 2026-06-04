from __future__ import annotations

from urllib.parse import urlparse


def is_safe_ai_base_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def is_shared_llm_configured(settings) -> bool:
    return bool(
        settings.content_ai_summary_enabled
        and settings.content_ai_summary_base_url
        and settings.content_ai_summary_api_key
        and settings.content_ai_summary_model
        and is_safe_ai_base_url(settings.content_ai_summary_base_url)
    )


def get_llm_feature_capabilities(settings) -> dict[str, object]:
    shared_configured = is_shared_llm_configured(settings)
    content_summary_enabled = shared_configured
    anomaly_reason_enabled = shared_configured and getattr(settings, "anomaly_ai_reason_enabled", False)
    fund_portfolio_risk_enabled = shared_configured and getattr(settings, "fund_portfolio_ai_risk_enabled", False)
    macro_analysis_enabled = (
        shared_configured
        and settings.macro_analysis_enabled
        and settings.macro_monitor_enabled
        and bool(settings.fred_api_key)
    )

    return {
        "enabled": content_summary_enabled or anomaly_reason_enabled or fund_portfolio_risk_enabled or macro_analysis_enabled,
        "reason": None if (content_summary_enabled or anomaly_reason_enabled or fund_portfolio_risk_enabled or macro_analysis_enabled) else "config_disabled",
        "features": {
            "contentSummary": content_summary_enabled,
            "anomalyReason": anomaly_reason_enabled,
            "fundPortfolioRiskAnalysis": fund_portfolio_risk_enabled,
            "macroAnalysis": macro_analysis_enabled,
        },
    }
