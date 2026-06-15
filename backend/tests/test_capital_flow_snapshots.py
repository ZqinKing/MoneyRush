from __future__ import annotations

import unittest

from app.services.market_detail.capital_flow_snapshots import enrich_snapshots_with_capital_flow


class FakeCapitalFlowQueryService:
    def __init__(self, flows: dict[str, dict[str, object]]) -> None:
        self.flows: dict[str, dict[str, object]] = flows

    async def fetch_latest_capital_flows(self, symbols: list[str]) -> dict[str, dict[str, object]]:
        return {symbol: self.flows[symbol] for symbol in symbols if symbol in self.flows}


class CapitalFlowSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_enriches_matching_snapshot_trade_day_capital_flow(self) -> None:
        snapshots: dict[str, dict[str, object]] = {"000001": {"updatedAt": "2026-06-12T07:01:00+00:00"}}
        query_service = FakeCapitalFlowQueryService(
            {
                "000001": {
                    "tradeDate": "2026-06-12",
                    "mainNetInflow": 123.0,
                    "mainNetRatio": 1.5,
                    "sourceStatus": "fresh",
                    "stale": False,
                }
            }
        )

        _ = await enrich_snapshots_with_capital_flow(snapshots=snapshots, symbols=["000001"], query_service=query_service)

        assert snapshots["000001"]["capitalFlowMainNetInflow"] == 123.0
        assert snapshots["000001"]["capitalFlowMainNetRatio"] == 1.5
        assert snapshots["000001"]["capitalFlowTradeDate"] == "2026-06-12"
        assert snapshots["000001"]["capitalFlowReferenceTradeDate"] == "2026-06-12"

    async def test_hides_mismatched_capital_flow_after_snapshot_trade_day_updates(self) -> None:
        snapshots: dict[str, dict[str, object]] = {"000001": {"updatedAt": "2026-06-15T01:45:00+00:00", "capitalFlowMainNetInflow": 999.0}}
        query_service = FakeCapitalFlowQueryService(
            {
                "000001": {
                    "tradeDate": "2026-06-12",
                    "mainNetInflow": 123.0,
                    "mainNetRatio": 1.5,
                    "sourceStatus": "fresh",
                    "stale": False,
                }
            }
        )

        _ = await enrich_snapshots_with_capital_flow(snapshots=snapshots, symbols=["000001"], query_service=query_service)

        assert "capitalFlowMainNetInflow" not in snapshots["000001"]
        assert "capitalFlowMainNetRatio" not in snapshots["000001"]
        assert snapshots["000001"]["capitalFlowTradeDate"] == "2026-06-12"
        assert snapshots["000001"]["capitalFlowReferenceTradeDate"] == "2026-06-15"
        assert snapshots["000001"]["capitalFlowStale"] is True

    async def test_keeps_last_trade_day_flow_when_snapshot_is_also_last_trade_day(self) -> None:
        snapshots: dict[str, dict[str, object]] = {"000001": {"updatedAt": "2026-06-12T07:01:00+00:00"}}
        query_service = FakeCapitalFlowQueryService(
            {
                "000001": {
                    "tradeDate": "2026-06-12",
                    "mainNetInflow": -50.0,
                    "mainNetRatio": -0.4,
                    "sourceStatus": "fresh",
                    "stale": False,
                }
            }
        )

        _ = await enrich_snapshots_with_capital_flow(snapshots=snapshots, symbols=["000001"], query_service=query_service)

        assert snapshots["000001"]["capitalFlowMainNetInflow"] == -50.0
        assert snapshots["000001"]["capitalFlowReferenceTradeDate"] == "2026-06-12"

    async def test_hides_capital_flow_when_snapshot_trade_day_is_unknown(self) -> None:
        snapshots: dict[str, dict[str, object]] = {"000001": {"updatedAt": "not-a-date", "capitalFlowMainNetInflow": 999.0}}
        query_service = FakeCapitalFlowQueryService(
            {
                "000001": {
                    "tradeDate": "2026-06-12",
                    "mainNetInflow": 123.0,
                    "mainNetRatio": 1.5,
                    "sourceStatus": "fresh",
                    "stale": False,
                }
            }
        )

        _ = await enrich_snapshots_with_capital_flow(snapshots=snapshots, symbols=["000001"], query_service=query_service)

        assert "capitalFlowMainNetInflow" not in snapshots["000001"]
        assert "capitalFlowMainNetRatio" not in snapshots["000001"]
        assert snapshots["000001"]["capitalFlowTradeDate"] == "2026-06-12"
        assert "capitalFlowReferenceTradeDate" not in snapshots["000001"]
        assert snapshots["000001"]["capitalFlowStale"] is True
