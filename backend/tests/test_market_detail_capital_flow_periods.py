from __future__ import annotations

from datetime import date, timedelta

from app.services.market_detail.query_service import build_capital_flow_periods


def _flow_row(
    trade_date: date,
    *,
    main_net_inflow: float | None,
    main_net_ratio: float | None,
    source: str = "eastmoney-direct",
    source_status: str = "fresh",
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "source": source,
        "source_status": source_status,
        "main_net_inflow": main_net_inflow,
        "main_net_ratio": main_net_ratio,
        "super_large_net_inflow": 2.0,
        "super_large_net_ratio": 1.0,
        "large_net_inflow": 3.0,
        "large_net_ratio": 1.5,
        "medium_net_inflow": -4.0,
        "medium_net_ratio": -2.0,
        "small_net_inflow": -5.0,
        "small_net_ratio": -2.5,
    }


def _period(periods: list[dict[str, object]], key: str) -> dict[str, object]:
    for item in periods:
        if item.get("period") == key:
            return item
    raise AssertionError(f"period not found: {key}")


def _tier(period: dict[str, object], key: str) -> dict[str, object]:
    tiers = period["tiers"]
    assert isinstance(tiers, list)
    for item in tiers:
        assert isinstance(item, dict)
        if item.get("key") == key:
            return item
    raise AssertionError(f"tier not found: {key}")


def test_builds_five_tier_periods_in_eastmoney_order() -> None:
    today = date(2026, 6, 30)
    rows = [_flow_row(today - timedelta(days=index), main_net_inflow=10.0, main_net_ratio=5.0) for index in range(10)]

    periods = build_capital_flow_periods(rows)
    ten_day = _period(periods, "10d")
    ten_day_tiers = ten_day["tiers"]
    assert isinstance(ten_day_tiers, list)

    assert [item["period"] for item in periods] == ["1d", "5d", "10d"]
    assert [item.get("key") for item in ten_day_tiers if isinstance(item, dict)] == ["main", "superLarge", "large", "medium", "small"]
    assert ten_day["sampleSize"] == 10
    assert ten_day["complete"] is True


def test_sums_period_net_inflow_and_reconstructs_ratio() -> None:
    today = date(2026, 6, 30)
    rows = [
        _flow_row(today - timedelta(days=index), main_net_inflow=10.0, main_net_ratio=5.0)
        for index in range(5)
    ]

    five_day = _period(build_capital_flow_periods(rows), "5d")
    main_tier = _tier(five_day, "main")

    assert main_tier["netInflow"] == 50.0
    assert main_tier["ratio"] == 5.0
    assert five_day["startTradeDate"] == "2026-06-26"
    assert five_day["endTradeDate"] == "2026-06-30"


def test_marks_incomplete_when_less_than_window_is_available() -> None:
    today = date(2026, 6, 30)
    rows = [_flow_row(today - timedelta(days=index), main_net_inflow=10.0, main_net_ratio=5.0) for index in range(3)]

    ten_day = _period(build_capital_flow_periods(rows), "10d")

    assert ten_day["sampleSize"] == 3
    assert ten_day["complete"] is False
    assert _tier(ten_day, "main")["netInflow"] == 30.0


def test_returns_null_ratio_when_denominator_cannot_be_reconstructed() -> None:
    today = date(2026, 6, 30)
    rows = [_flow_row(today, main_net_inflow=10.0, main_net_ratio=0.0)]

    periods = build_capital_flow_periods(rows)
    one_day = _period(periods, "1d")
    ten_day = _period(periods, "10d")

    assert _tier(one_day, "main")["ratio"] == 0.0
    assert _tier(ten_day, "main")["ratio"] is None


def test_includes_numeric_stale_rows_and_excludes_no_data_placeholders() -> None:
    today = date(2026, 6, 30)
    rows = [
        _flow_row(today, main_net_inflow=None, main_net_ratio=None, source="capital-flow-unavailable", source_status="stale"),
        _flow_row(today - timedelta(days=1), main_net_inflow=20.0, main_net_ratio=4.0, source_status="stale"),
        _flow_row(today - timedelta(days=2), main_net_inflow=10.0, main_net_ratio=5.0),
    ]

    periods = build_capital_flow_periods(rows)
    one_day = _period(periods, "1d")
    five_day = _period(periods, "5d")

    assert one_day["tradeDate"] == "2026-06-29"
    assert _tier(one_day, "main")["netInflow"] == 20.0
    assert five_day["sampleSize"] == 2
    assert _tier(five_day, "main")["netInflow"] == 30.0
