from __future__ import annotations

from datetime import UTC, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from collector.workers.dragon_tiger_loop import CHINA_MARKET_TZ, DragonTigerCollectorWorker


def _settings(**overrides):
    defaults = {
        "redis_url": "redis://localhost:6379/0",
        "postgres_dsn": "postgresql://example",
        "collector_enable_runtime_data_repair": False,
        "dragon_tiger_request_timeout_seconds": 1.0,
        "dragon_tiger_request_retry_attempts": 0,
        "dragon_tiger_request_retry_backoff_seconds": 0.0,
        "dragon_tiger_collection_start_hour_china": 17,
        "dragon_tiger_collection_start_minute_china": 10,
        "dragon_tiger_no_data_grace_seconds": 10800,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _worker():
    with patch("collector.workers.dragon_tiger_loop.Redis.from_url"), patch("collector.workers.dragon_tiger_loop.PostgresStore"), patch("collector.workers.dragon_tiger_loop.DragonTigerClient"):
        return DragonTigerCollectorWorker(_settings())


def test_current_trade_date_before_due_time_is_not_due():
    worker = _worker()
    fixed_now = datetime(2026, 6, 8, 14, 30, tzinfo=CHINA_MARKET_TZ)

    with patch("collector.workers.dragon_tiger_loop.datetime") as fake_datetime:
        fake_datetime.now.return_value = fixed_now
        fake_datetime.combine = datetime.combine
        fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        assert worker._classify_target_trade_date_state(fixed_now.date()) == "not_due"


def test_weekend_target_date_is_no_trade_day_without_vendor_call():
    worker = _worker()
    saturday = datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc).astimezone(CHINA_MARKET_TZ).date()

    assert worker._classify_target_trade_date_state(saturday) == "no_trade_day"


def test_due_weekday_after_collection_time_is_due():
    worker = _worker()
    fixed_now = datetime(2026, 6, 8, 18, 30, tzinfo=CHINA_MARKET_TZ)

    with patch("collector.workers.dragon_tiger_loop.datetime") as fake_datetime:
        fake_datetime.now.return_value = fixed_now
        fake_datetime.combine = datetime.combine
        fake_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        assert worker._classify_target_trade_date_state(fixed_now.date()) == "due"
