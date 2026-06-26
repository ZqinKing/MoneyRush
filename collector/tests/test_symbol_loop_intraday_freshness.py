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
        async def smembers(self, _key: str) -> set[str]:
            return {"000001", "005930", "300750"}

    class FakeSettings:
        active_symbols_key: str

        active_symbols_key = "moneyrush:active_symbols"

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

    collect_active_symbols = cast(Callable[[], Coroutine[object, object, None]], getattr(worker, "_collect_active_symbols"))
    asyncio.run(collect_active_symbols())

    assert ("collect", "300750") in calls
    assert ("intraday", "300750") in calls
    assert ("intraday", "005930") not in calls
