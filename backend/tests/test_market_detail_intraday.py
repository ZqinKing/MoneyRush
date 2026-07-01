from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

from app.services.market_detail.query_service import MarketDetailQueryService


CHINA_MARKET_TZ = timezone(timedelta(hours=8))


def test_fetch_intraday_sampled_bars_uses_reference_trade_day() -> None:
    service = MarketDetailQueryService.__new__(MarketDetailQueryService)
    queries: list[tuple[object, ...]] = []

    async def fake_fetch(_query: str, *args: object) -> list[dict[str, object]]:
        queries.append(args)
        day_start = args[1]
        assert isinstance(day_start, datetime)
        assert day_start.astimezone(CHINA_MARKET_TZ).date().isoformat() == "2026-06-30"
        return [
            {
                "bucket_ts": datetime(2026, 6, 30, 1, 30, tzinfo=UTC),
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100,
                "amount": 1000.0,
                "source": "mootdx",
                "raw": {"provider": "mootdx", "quality": "vendor_verified", "synthetic": False},
            }
        ]

    async def latest_window_should_not_run(_symbol: str):
        raise AssertionError("reference_ts should avoid the latest intraday window lookup")

    setattr(service, "_fetch", fake_fetch)
    setattr(service, "_latest_intraday_trade_day_window", latest_window_should_not_run)

    bars = asyncio.run(
        service.fetch_intraday_sampled_bars(
            "000001",
            interval_minutes=1,
            allow_tick_fallback=False,
            reference_ts="2026-06-30T15:00:00+08:00",
        )
    )

    assert len(bars) == 1
    assert bars[0]["bucketTs"] == "2026-06-30T01:30:00+00:00"
    assert queries


def test_fetch_order_book_expands_mootdx_depth_levels() -> None:
    service = MarketDetailQueryService.__new__(MarketDetailQueryService)

    async def fake_fetchrow(_query: str, *_args: object) -> dict[str, object]:
        return {
            "raw": {
                "provider": "mootdx",
                "bid1": 10.0,
                "bidVolume1": 100,
                "ask1": 10.1,
                "askVolume1": 200,
                "quote": {
                    "bid2": 9.99,
                    "bid_vol2": 3,
                    "bid3": 9.98,
                    "bid_vol3": 4,
                    "bid4": 9.97,
                    "bid_vol4": 5,
                    "bid5": 9.96,
                    "bid_vol5": 6,
                    "ask2": 10.11,
                    "ask_vol2": 7,
                    "ask3": 10.12,
                    "ask_vol3": 8,
                    "ask4": 10.13,
                    "ask_vol4": 9,
                    "ask5": 10.14,
                    "ask_vol5": 10,
                },
            }
        }

    setattr(service, "_fetchrow", fake_fetchrow)

    order_book = asyncio.run(service.fetch_order_book("000001"))

    assert order_book["bid1"] == 10.0
    assert order_book["bidVolume1"] == 100
    assert order_book["ask1"] == 10.1
    assert order_book["askVolume1"] == 200
    bids = order_book["bids"]
    asks = order_book["asks"]
    assert isinstance(bids, list)
    assert isinstance(asks, list)
    assert bids[-1] == {"level": 5, "price": 9.96, "volume": 600}
    assert asks[-1] == {"level": 5, "price": 10.14, "volume": 1000}
