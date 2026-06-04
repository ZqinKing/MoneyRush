from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "MoneyRush API"
    app_env: str = "development"
    frontend_origin: str = "http://localhost:5173"
    frontend_origin_regex: str | None = r"https?://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:5173)?$"
    redis_url: str = "redis://redis:6379/0"
    postgres_dsn: str = "postgresql://moneyrush:moneyrush@db:5432/moneyrush"
    redis_stream_key: str = "moneyrush:symbol:commands"
    active_symbols_key: str = "moneyrush:active_symbols"
    market_snapshot_key_prefix: str = "moneyrush:snapshot"
    market_event_key_prefix: str = "moneyrush:event"
    market_events_stream_key: str = "moneyrush:market:events"
    market_overview_cache_key: str = "moneyrush:market:overview"
    gold_dashboard_cache_key: str = "moneyrush:gold:dashboard"
    active_funds_key: str = "moneyrush:active_funds"
    fund_snapshot_key_prefix: str = "moneyrush:fund:snapshot"
    fund_holdings_key_prefix: str = "moneyrush:fund"
    fund_auto_link_stocks_key_prefix: str = "moneyrush:fund:auto_link"
    stock_funds_key_prefix: str = "moneyrush:stock"
    ws_heartbeat_interval_seconds: int = 2
    collector_intraday_post_close_reconciliation_seconds: int = 900
    content_query_cache_ttl_seconds: int = 120
    content_feed_cache_key_prefix: str = "moneyrush:content:feed"
    content_status_cache_key_prefix: str = "moneyrush:content:status"
    dragon_tiger_cache_key_prefix: str = "moneyrush:dragon_tiger"
    dragon_tiger_cache_ttl_seconds: int = 300
    dragon_tiger_stale_cache_ttl_seconds: int = 43200
    dragon_tiger_request_timeout_seconds: float = 15.0
    dragon_tiger_request_retry_attempts: int = 3
    dragon_tiger_request_retry_backoff_seconds: float = 0.6
    content_report_refresh_seconds: int = 43200
    content_news_refresh_seconds: int = 1800
    content_announcement_refresh_seconds: int = 7200
    content_market_news_refresh_seconds: int = 900
    content_ai_summary_enabled: bool = False
    content_ai_summary_base_url: str | None = None
    content_ai_summary_api_key: str | None = None
    content_ai_summary_model: str | None = None
    content_ai_summary_timeout_seconds: int = 45
    content_ai_summary_max_retries: int = 2
    content_ai_summary_temperature: float = 0.0
    content_ai_summary_max_completion_tokens: int = 8192
    content_ai_summary_max_input_chars: int = 131072
    anomaly_ai_reason_enabled: bool = False
    macro_monitor_enabled: bool = True
    fred_api_key: str | None = None
    macro_analysis_enabled: bool = True
    macro_ten_year_warning_threshold: float = 4.8
    macro_snapshot_cache_key: str = "moneyrush:macro:snapshot"
    macro_analysis_latest_cache_key: str = "moneyrush:macro:analysis:latest"
    macro_collector_status_cache_key: str = "moneyrush:macro:collector_status"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
