from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "MoneyRush API"
    app_env: str = "development"
    frontend_origin: str = "http://localhost:5173"
    frontend_origin_regex: str = r"https?://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:5173)?$"
    redis_url: str = "redis://redis:6379/0"
    postgres_dsn: str = "postgresql://moneyrush:moneyrush@db:5432/moneyrush"
    redis_stream_key: str = "moneyrush:symbol:commands"
    active_symbols_key: str = "moneyrush:active_symbols"
    market_snapshot_key_prefix: str = "moneyrush:snapshot"
    market_event_key_prefix: str = "moneyrush:event"
    market_events_stream_key: str = "moneyrush:market:events"
    ws_heartbeat_interval_seconds: int = 2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
