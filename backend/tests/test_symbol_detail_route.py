from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.api.routes.symbols import symbol_detail


class FakeRedisStore:
    async def get_symbol_snapshot(self, _symbol: str) -> dict[str, object]:
        return {"updatedAt": "2026-06-30T15:00:00+08:00", "lastPrice": 10.0, "source": "mootdx+tencent-finance"}

    async def get_symbol_event(self, _symbol: str) -> None:
        return None


class FakeFundQueryService:
    async def fetch_stock_funds(self, _symbol: str) -> dict[str, object]:
        return {"items": []}


class FakeQueryService:
    def __init__(self) -> None:
        self.intraday_calls: list[dict[str, object]] = []

    async def fetch_snapshot(self, _symbol: str) -> None:
        return None

    async def fetch_latest_event(self, _symbol: str) -> None:
        return None

    async def fetch_latest_kline(self, _symbol: str, *, period: str) -> dict[str, object]:
        assert period == "1d"
        return {"bucketTs": "2026-06-30T00:00:00+00:00"}

    async def fetch_latest_capital_flow(self, _symbol: str) -> None:
        return None

    async def fetch_klines(self, _symbol: str, *, period: str, limit: int) -> list[dict[str, object]]:
        assert period == "1d"
        assert limit == 30
        return []

    async def fetch_intraday_sampled_bars(self, symbol: str, *, interval_minutes: int, allow_tick_fallback: bool = True, reference_ts: object = None) -> list[dict[str, object]]:
        self.intraday_calls.append(
            {
                "symbol": symbol,
                "interval_minutes": interval_minutes,
                "allow_tick_fallback": allow_tick_fallback,
                "reference_ts": reference_ts,
            }
        )
        return [
            {
                "bucketTs": "2026-06-30T01:30:00+00:00",
                "close": 10.0,
            },
            {
                "bucketTs": "2026-06-30T01:31:00+00:00",
                "close": 10.1,
            },
        ]

    async def fetch_order_book(self, _symbol: str) -> dict[str, object]:
        return {
            "bid1": 10.0,
            "bidVolume1": 100,
            "ask1": 10.1,
            "askVolume1": 200,
            "bids": [{"level": level, "price": 10.0 - level / 100, "volume": level * 100} for level in range(1, 6)],
            "asks": [{"level": level, "price": 10.1 + level / 100, "volume": level * 200} for level in range(1, 6)],
        }

    async def fetch_intraday_completeness(self, _symbol: str, *, reference_ts: object, reconciliation_seconds: int) -> dict[str, object]:
        assert reference_ts == "2026-06-30T15:00:00+08:00"
        assert reconciliation_seconds == 60
        return {"tradeDay": "2026-06-30", "status": "complete"}


def test_symbol_detail_passes_reference_ts_to_intraday_queries_and_enables_depth5() -> None:
    query_service = FakeQueryService()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                redis_store=FakeRedisStore(),
                market_detail_query_service=query_service,
                fund_query_service=FakeFundQueryService(),
                settings=SimpleNamespace(collector_intraday_post_close_reconciliation_seconds=60),
            )
        )
    )

    payload = asyncio.run(symbol_detail("000001", request))
    intraday_completeness = payload["intradayCompleteness"]
    capabilities = payload["capabilities"]
    order_book = payload["orderBook"]
    assert isinstance(intraday_completeness, dict)
    assert isinstance(capabilities, dict)
    assert isinstance(order_book, dict)
    bids = order_book["bids"]
    assert isinstance(bids, list)
    last_bid = bids[-1]
    assert isinstance(last_bid, dict)
    assert [call["reference_ts"] for call in query_service.intraday_calls] == ["2026-06-30T15:00:00+08:00"] * 2
    assert intraday_completeness["tradeDay"] == "2026-06-30"
    assert capabilities["supportsBestBidAsk"] is True
    assert capabilities["supportsOrderBookDepth5"] is True
    assert last_bid["level"] == 5
