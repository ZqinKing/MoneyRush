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
    collector_poll_interval_seconds: int = 5
    collector_symbol_min_interval_seconds: int = 10
    collector_tencent_enrichment_interval_seconds: int = 600
    collector_vendor_failure_cooldown_seconds: int = 60
    collector_enable_runtime_data_repair: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
