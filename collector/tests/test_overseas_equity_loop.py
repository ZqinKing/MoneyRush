from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from collector.workers.overseas_equity_loop import OverseasEquityCollectorWorker


def test_builds_us_realtime_intraday_bar_during_regular_session() -> None:
    bar = OverseasEquityCollectorWorker._build_realtime_intraday_bar(
        _market_state("TSM.US", market="US", ts=datetime(2026, 7, 2, 14, 30, tzinfo=UTC))
    )

    assert bar is not None
    assert bar["symbol"] == "TSM.US"
    assert bar["period"] == "1m"
    assert bar["bucketTs"] == datetime(2026, 7, 2, 14, 30, tzinfo=UTC)
    assert bar["source"] == "overseas-realtime-aggregated"
    assert cast(dict[str, object], bar["raw"])["market"] == "US"


def test_skips_us_realtime_intraday_bar_after_regular_session() -> None:
    bar = OverseasEquityCollectorWorker._build_realtime_intraday_bar(
        _market_state("TSM.US", market="US", ts=datetime(2026, 7, 2, 20, 0, tzinfo=UTC))
    )

    assert bar is None


def test_builds_hk_realtime_intraday_bar_during_afternoon_session() -> None:
    bar = OverseasEquityCollectorWorker._build_realtime_intraday_bar(
        _market_state("00700.HK", market="HK", ts=datetime(2026, 7, 3, 5, 0, tzinfo=UTC))
    )

    assert bar is not None
    assert bar["symbol"] == "00700.HK"
    assert bar["bucketTs"] == datetime(2026, 7, 3, 5, 0, tzinfo=UTC)
    assert cast(dict[str, object], bar["raw"])["market"] == "HK"


def test_skips_hk_realtime_intraday_bar_during_lunch_break() -> None:
    bar = OverseasEquityCollectorWorker._build_realtime_intraday_bar(
        _market_state("00700.HK", market="HK", ts=datetime(2026, 7, 3, 4, 30, tzinfo=UTC))
    )

    assert bar is None


def test_skips_eod_fallback_for_realtime_intraday_bar() -> None:
    state = _market_state("AAPL.US", market="US", ts=datetime(2026, 7, 2, 14, 30, tzinfo=UTC))
    state["snapshot"]["source"] = "stooq-eod"

    assert OverseasEquityCollectorWorker._build_realtime_intraday_bar(state) is None


def _market_state(symbol: str, *, market: str, ts: datetime) -> dict[str, dict[str, object]]:
    return {
        "snapshot": {
            "symbol": symbol,
            "market": market,
            "source": "sina-finance",
            "updatedAt": ts.isoformat(),
        },
        "tick": {
            "symbol": symbol,
            "ts": ts,
            "price": 123.45,
            "volume": 1000,
            "amount": 123450.0,
        },
    }
