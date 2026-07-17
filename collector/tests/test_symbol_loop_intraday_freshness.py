import asyncio
from collections.abc import Coroutine
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Callable, cast

from collector.workers.symbol_loop import CollectorWorker


CHINA_MARKET_TZ = timezone(timedelta(hours=8))


def test_market_state_trade_day_replaces_stale_intraday_terminal_state() -> None:
    worker = CollectorWorker.__new__(CollectorWorker)
    setattr(worker, "_latest_daily_trade_day_by_symbol", {"000001": date(2026, 6, 25)})
    setattr(worker, "_intraday_history_terminal_for_trade_day", {"000001": ("2026-06-25", "complete")})
    setattr(worker, "_intraday_history_last_refresh_at", {"000001": 123.0})
    setattr(worker, "_intraday_history_last_attempt_at_by_key", {("000001", "2026-06-25"): 123.0})

    getattr(worker, "_remember_market_state_trade_day")(
        "000001",
        {
            "snapshot": {
                "updatedAt": datetime(2026, 6, 26, 10, 0, tzinfo=CHINA_MARKET_TZ).astimezone(UTC).isoformat()
            },
            "tick": {},
            "kline": {},
        },
    )

    assert getattr(worker, "_latest_daily_trade_day_by_symbol")["000001"] == date(2026, 6, 26)
    assert "000001" not in getattr(worker, "_intraday_history_terminal_for_trade_day")
    assert "000001" not in getattr(worker, "_intraday_history_last_refresh_at")
    assert ("000001", "2026-06-25") not in getattr(worker, "_intraday_history_last_attempt_at_by_key")


def test_market_state_trade_day_uses_tick_when_snapshot_timestamp_missing() -> None:
    trade_ts = datetime(2026, 6, 26, 9, 31, tzinfo=CHINA_MARKET_TZ).astimezone(UTC)

    assert getattr(CollectorWorker, "_market_state_trade_day")(
        {
            "snapshot": {},
            "tick": {"ts": trade_ts},
            "kline": {},
        }
    ) == date(2026, 6, 26)


def test_market_state_trade_day_treats_naive_timestamp_as_china_time() -> None:
    assert getattr(CollectorWorker, "_market_state_trade_day")(
        {
            "snapshot": {"updatedAt": "2026-06-26T00:30:00"},
            "tick": {},
            "kline": {},
        }
    ) == date(2026, 6, 26)


def test_collect_active_symbols_continues_after_single_symbol_failure() -> None:
    worker = CollectorWorker.__new__(CollectorWorker)
    calls: list[tuple[str, str]] = []

    class FakeRedis:
        def __init__(self) -> None:
            self.sets = {
                "moneyrush:active_symbols": {"000001", "005930", "300750"},
                "moneyrush:active_symbols:manual": {"005930"},
            }
            self.deleted_keys: list[str] = []

        async def smembers(self, _key: str) -> set[str]:
            return set(self.sets.get(_key, set()))

        async def srem(self, key: str, *values: str) -> None:
            self.sets.setdefault(key, set()).difference_update(values)

        async def delete(self, *keys: str) -> None:
            self.deleted_keys.extend(keys)

    class FakeSettings:
        active_symbols_key: str

        active_symbols_key = "moneyrush:active_symbols"
        market_snapshot_key_prefix = "moneyrush:market:snapshot"
        market_event_key_prefix = "moneyrush:market:event"

    async def safe_daily(symbol: str) -> None:
        calls.append(("daily", symbol))

    async def collect_symbol(symbol: str) -> None:
        calls.append(("collect", symbol))
        if symbol == "005930":
            raise ValueError("unsupported quote payload")

    async def safe_intraday(symbol: str) -> None:
        calls.append(("intraday", symbol))

    setattr(worker, "_redis", FakeRedis())
    setattr(worker, "_settings", FakeSettings())
    setattr(worker, "_safe_ensure_daily_history", safe_daily)
    setattr(worker, "_collect_symbol", collect_symbol)
    setattr(worker, "_safe_ensure_intraday_history", safe_intraday)
    setattr(worker, "_last_collected_at", {"005930": 1.0})
    setattr(worker, "_last_market_state_identity", {"005930": (1,)})
    setattr(worker, "_symbol_poll_interval_seconds", {"005930": 1.0})
    setattr(worker, "_unchanged_quote_counts", {"005930": 1})
    setattr(worker, "_daily_history_synced_for_trade_day", {"005930": "2026-06-26"})
    setattr(worker, "_intraday_history_terminal_for_trade_day", {"005930": ("2026-06-26", "complete")})
    setattr(worker, "_intraday_history_last_refresh_at", {"005930": 1.0})
    setattr(worker, "_intraday_history_last_attempt_at_by_key", {("005930", "2026-06-26"): 1.0})
    setattr(worker, "_latest_daily_trade_day_by_symbol", {"005930": date(2026, 6, 26)})

    collect_active_symbols = cast(Callable[[], Coroutine[object, object, None]], getattr(worker, "_collect_active_symbols"))
    asyncio.run(collect_active_symbols())

    redis = getattr(worker, "_redis")
    assert ("collect", "300750") in calls
    assert ("intraday", "300750") in calls
    assert ("collect", "005930") not in calls
    assert ("intraday", "005930") not in calls
    assert "005930" not in redis.sets["moneyrush:active_symbols"]
    assert "005930" not in redis.sets["moneyrush:active_symbols:manual"]
    assert "moneyrush:market:snapshot:005930" in redis.deleted_keys
    assert "005930" not in getattr(worker, "_latest_daily_trade_day_by_symbol")


def test_intraday_failed_historical_attempt_is_throttled_without_terminal_state() -> None:
    worker = CollectorWorker.__new__(CollectorWorker)

    class FakeSettings:
        collector_intraday_history_enabled = True
        collector_intraday_history_refresh_seconds = 3600
        collector_intraday_history_retry_seconds = 120
        collector_intraday_post_close_reconciliation_seconds = 0

    class FakePostgres:
        async def has_complete_intraday_history(self, **_kwargs) -> bool:
            return False

        async def persist_kline_history(self, _history) -> None:
            raise AssertionError("failed fetch should not persist")

    class FailingQuoteClient:
        def __init__(self) -> None:
            self.fetch_count = 0

        def fetch_intraday_history(self, _symbol: str, _trade_day: date) -> list[dict[str, object]]:
            self.fetch_count += 1
            raise ValueError("upstream disconnected")

    quote_client = FailingQuoteClient()
    setattr(worker, "_settings", FakeSettings())
    setattr(worker, "_postgres", FakePostgres())
    setattr(worker, "_quote_client", quote_client)
    setattr(worker, "_latest_daily_trade_day_by_symbol", {"000001": date(2026, 6, 26)})
    setattr(worker, "_intraday_history_terminal_for_trade_day", {})
    setattr(worker, "_intraday_history_last_refresh_at", {})
    setattr(worker, "_intraday_history_last_attempt_at_by_key", {})

    asyncio.run(worker._safe_ensure_intraday_history("000001"))
    asyncio.run(worker._safe_ensure_intraday_history("000001"))

    assert quote_client.fetch_count == 1
    assert getattr(worker, "_intraday_history_terminal_for_trade_day") == {}
    assert ("000001", "2026-06-26") in getattr(worker, "_intraday_history_last_attempt_at_by_key")

    getattr(worker, "_latest_daily_trade_day_by_symbol")["000001"] = date(2026, 6, 25)
    asyncio.run(worker._safe_ensure_intraday_history("000001"))

    assert quote_client.fetch_count == 2


def test_intraday_failed_current_day_attempt_uses_retry_interval_not_refresh_interval() -> None:
    worker = CollectorWorker.__new__(CollectorWorker)

    class FakeSettings:
        collector_intraday_history_enabled = True
        collector_intraday_history_refresh_seconds = 3600
        collector_intraday_history_retry_seconds = 120
        collector_intraday_post_close_reconciliation_seconds = 0

    setattr(worker, "_settings", FakeSettings())
    setattr(worker, "_intraday_history_last_attempt_at_by_key", {})

    trade_day = datetime.now(CHINA_MARKET_TZ).date()
    worker._record_intraday_attempt("000001", trade_day)

    assert worker._should_skip_intraday_attempt("000001", trade_day)
    key = worker._intraday_attempt_key("000001", trade_day)
    getattr(worker, "_intraday_history_last_attempt_at_by_key")[key] -= 121
    assert not worker._should_skip_intraday_attempt("000001", trade_day)


def test_intraday_persisted_complete_history_skips_vendor_fetch() -> None:
    worker = CollectorWorker.__new__(CollectorWorker)

    class FakeSettings:
        collector_intraday_history_enabled = True
        collector_intraday_history_refresh_seconds = 3600
        collector_intraday_post_close_reconciliation_seconds = 0

    class FakePostgres:
        async def has_complete_intraday_history(self, **kwargs) -> bool:
            assert kwargs["symbol"] == "000001"
            assert kwargs["trade_day"] == date(2026, 6, 26)
            return True

        async def persist_kline_history(self, _history) -> None:
            raise AssertionError("complete persisted history should not persist")

    class FailingQuoteClient:
        def fetch_intraday_history(self, _symbol: str, _trade_day: date) -> list[dict[str, object]]:
            raise AssertionError("complete persisted history should skip vendor fetch")

    setattr(worker, "_settings", FakeSettings())
    setattr(worker, "_postgres", FakePostgres())
    setattr(worker, "_quote_client", FailingQuoteClient())
    setattr(worker, "_latest_daily_trade_day_by_symbol", {"000001": date(2026, 6, 26)})
    setattr(worker, "_intraday_history_terminal_for_trade_day", {})
    setattr(worker, "_intraday_history_last_refresh_at", {})
    setattr(worker, "_intraday_history_last_attempt_at_by_key", {})

    asyncio.run(worker._ensure_intraday_history("000001"))

    assert getattr(worker, "_intraday_history_terminal_for_trade_day") == {"000001": ("2026-06-26", "complete")}
