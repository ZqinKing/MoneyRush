from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    redis_url: str = "redis://redis:6379/0"
    postgres_dsn: str = "postgresql://moneyrush:moneyrush@db:5432/moneyrush"
    redis_stream_key: str = "moneyrush:symbol:commands"
    active_symbols_key: str = "moneyrush:active_symbols"
    market_snapshot_key_prefix: str = "moneyrush:snapshot"
    market_event_key_prefix: str = "moneyrush:event"
    market_events_stream_key: str = "moneyrush:market:events"
    market_overview_cache_key: str = "moneyrush:market:overview"
    active_funds_key: str = "moneyrush:active_funds"
    fund_snapshot_key_prefix: str = "moneyrush:fund:snapshot"
    fund_holdings_key_prefix: str = "moneyrush:fund"
    fund_auto_link_stocks_key_prefix: str = "moneyrush:fund:auto_link"
    stock_funds_key_prefix: str = "moneyrush:stock"
    collector_poll_interval_seconds: int = 5
    collector_symbol_min_interval_seconds: int = 10
    collector_unchanged_quote_backoff_threshold: int = 2
    collector_unchanged_quote_backoff_base_seconds: int = 30
    collector_unchanged_quote_backoff_max_seconds: int = 300
    collector_tencent_enrichment_interval_seconds: int = 600
    collector_vendor_failure_cooldown_seconds: int = 60
    collector_intraday_history_enabled: bool = True
    collector_intraday_history_refresh_seconds: int = 1800
    collector_intraday_history_request_interval_seconds: int = 8
    collector_intraday_history_request_jitter_seconds: int = 2
    collector_intraday_history_vendor_cooldown_seconds: int = 3600
    collector_intraday_post_close_reconciliation_seconds: int = 900
    collector_enable_runtime_data_repair: bool = False
    collector_vendor_price_divergence_limit_pct: float = 15.0
    anomaly_aggregation_enabled: bool = True
    anomaly_aggregation_interval_seconds: int = 300
    anomaly_ai_reason_enabled: bool = False
    anomaly_ai_reason_interval_seconds: int = 300
    anomaly_ai_reason_batch_size: int = 10
    content_collector_enabled: bool = True
    content_collector_poll_interval_seconds: int = 5
    content_collector_batch_size: int = 3
    content_fetch_min_interval_seconds: int = 10
    content_fetch_jitter_seconds: int = 2
    content_fetch_cooldown_base_seconds: int = 1800
    content_report_refresh_seconds: int = 43200
    content_news_refresh_seconds: int = 1800
    content_announcement_refresh_seconds: int = 7200
    content_market_news_refresh_seconds: int = 900
    market_overview_collector_enabled: bool = True
    market_overview_refresh_seconds: int = 30
    market_overview_tencent_fallback_enabled: bool = True
    market_overview_tencent_refresh_seconds: int = 120
    market_overview_tencent_failure_cooldown_seconds: int = 180
    market_overview_legu_breadth_enabled: bool = True
    market_overview_legu_breadth_refresh_seconds: int = 300
    market_overview_legu_breadth_timeout_seconds: int = 10
    market_overview_legu_breadth_failure_cooldown_seconds: int = 600
    fund_collector_enabled: bool = True
    fund_collector_poll_interval_seconds: int = 3600
    fund_collector_request_interval_seconds: float = 1.0
    content_report_backfill_days: int = 365
    content_announcement_backfill_pages: int = 5
    content_news_backfill_max_items: int = 100
    content_news_detail_fetch_max_items: int = 5
    content_news_detail_fetch_max_age_seconds: int = 1800
    content_ai_summary_enabled: bool = False
    content_ai_summary_base_url: str | None = None
    content_ai_summary_api_key: str | None = None
    content_ai_summary_model: str | None = None
    content_ai_summary_timeout_seconds: int = 45
    content_ai_summary_max_retries: int = 2
    content_ai_summary_temperature: float = 0.0
    content_ai_summary_max_completion_tokens: int = 8192
    content_ai_summary_max_input_chars: int = 131072
    content_ai_summary_min_content_length: int = 180
    content_ai_summary_max_news_age_seconds: int = 1800
    content_ai_summary_prompt_version: str = "v1"
    dragon_tiger_collector_enabled: bool = True
    dragon_tiger_collector_poll_interval_seconds: int = 300
    dragon_tiger_collection_start_hour_china: int = 17
    dragon_tiger_collection_start_minute_china: int = 10
    dragon_tiger_request_timeout_seconds: float = 15.0
    dragon_tiger_request_retry_attempts: int = 3
    dragon_tiger_request_retry_backoff_seconds: float = 0.6
    macro_monitor_enabled: bool = True
    fred_api_key: str | None = None
    macro_collector_enabled: bool = True
    macro_collector_refresh_seconds: int = 21600
    macro_fred_observation_lookback_days: int = 45
    macro_fred_request_timeout_seconds: float = 15.0
    macro_fred_failure_cooldown_seconds: int = 1800
    macro_analysis_enabled: bool = False
    macro_analysis_daily_digest_enabled: bool = False
    macro_analysis_daily_digest_hour_utc: int = 22
    macro_ten_year_warning_threshold: float = 4.8
    macro_snapshot_cache_key: str = "moneyrush:macro:snapshot"
    macro_analysis_latest_cache_key: str = "moneyrush:macro:analysis:latest"
    macro_collector_status_cache_key: str = "moneyrush:macro:collector_status"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
