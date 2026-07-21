from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from typing import cast

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


def test_persisted_daily_anomaly_related_funds_use_latest_active_fund_holdings() -> None:
    service = MarketDetailQueryService.__new__(MarketDetailQueryService)
    queries: list[str] = []

    async def fake_fetch(query: str, *args: object) -> list[dict[str, object]]:
        queries.append(query)
        if "FROM significant_anomaly" in query:
            return [_anomaly_row()]
        if "FROM anomaly_post_close_review_checkpoint" in query:
            return []
        if "FROM fund_stock_holding" in query:
            assert "stock_fund_holding" in query
            assert "AND report_date = fsh.report_date" in query
            assert args[0] == ["688630"]
            assert args[1] == ["018815"]
            return [_fund_holding_row()]
        raise AssertionError(f"unexpected query: {query}")

    setattr(service, "_fetch", fake_fetch)

    report = asyncio.run(
        service.fetch_daily_anomaly_report(
            symbols=["688630"],
            active_funds=["018815"],
            report_date="2026-07-21",
            severities={"critical"},
        )
    )

    portfolio_anomalies = report["portfolioAnomalies"]
    assert isinstance(portfolio_anomalies, list)
    item = cast(dict[str, object], portfolio_anomalies[0])
    related_funds = item["relatedFunds"]
    assert related_funds == [
        {
            "fundCode": "018815",
            "fundName": "方正富邦核心优势混合A",
            "fundType": "混合型-偏股",
            "reportDate": "2026-06-30",
            "stockWeightInFund": 4.44,
            "holdMarketValue": 549390000.0,
            "changeType": None,
            "estimatedImpact": 0.616272,
        }
    ]
    assert any("FROM fund_stock_holding" in query for query in queries)


def test_fallback_daily_anomaly_related_fund_lookup_uses_fund_primary_table() -> None:
    service = MarketDetailQueryService.__new__(MarketDetailQueryService)
    queries: list[str] = []

    async def no_persisted_report(**_kwargs: object) -> None:
        return None

    async def fake_fetch(query: str, *_args: object) -> list[dict[str, object]]:
        queries.append(query)
        if "FROM stock_event" in query:
            return []
        if "FROM stock_snapshot" in query:
            return []
        if "FROM stock_kline" in query:
            return []
        if "FROM fund_stock_holding" in query:
            assert "stock_fund_holding" in query
            assert "AND report_date = fsh.report_date" in query
            return []
        raise AssertionError(f"unexpected query: {query}")

    setattr(service, "_fetch_persisted_daily_anomaly_report", no_persisted_report)
    setattr(service, "_fetch", fake_fetch)

    report = asyncio.run(
        service.fetch_daily_anomaly_report(
            symbols=["688630"],
            active_funds=["018815"],
            report_date="2026-07-21",
            severities={"critical"},
        )
    )

    assert report["portfolioAnomalies"] == []
    assert any("FROM fund_stock_holding" in query for query in queries)


def _anomaly_row() -> dict[str, object]:
    trigger_time = datetime(2026, 7, 21, 4, 58, 59, tzinfo=UTC)
    return {
        "symbol": "688630",
        "snapshot_payload": {"companyName": "芯碁微装"},
        "payload": {"changePct": 13.88, "strongestPriceJumpPct": 1.56},
        "change_pct": 13.88,
        "anomaly_type": "price_jump",
        "severity": "critical",
        "trigger_price": 383.88,
        "first_trigger_ts": trigger_time,
        "first_trigger_bucket": trigger_time.replace(minute=0, second=0),
        "volume_ratio": 1.33,
        "event_count": 1282,
        "ai_reason": None,
        "ai_reason_status": "pending",
        "ai_reason_generated_at": None,
        "ai_reason_phase": "intraday",
        "ai_reason_evidence_cutoff_at": None,
        "ai_reason_includes_dragon_tiger": False,
        "ai_reason_post_close_required": True,
        "ai_reason_post_close_status": "not_due",
        "ai_reason_post_close_generated_at": None,
        "ai_reason_post_close": None,
        "related_news_ids": [],
        "related_announcement_ids": [],
    }


def _fund_holding_row() -> dict[str, object]:
    return {
        "stock_symbol": "688630",
        "fund_code": "018815",
        "fund_name": "方正富邦核心优势混合A",
        "fund_type": "混合型-偏股",
        "report_date": date(2026, 6, 30),
        "weight_percent": 4.44,
        "hold_market_value": 549390000.0,
        "change_type": None,
    }
