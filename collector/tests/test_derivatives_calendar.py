from __future__ import annotations

from datetime import date

from collector.services.derivatives_calendar import generate_derivatives_events


def _event_by_kind(events: list[dict[str, object]], kind: str) -> dict[str, object]:
    matches = [event for event in events if event.get("event_kind") == kind]
    assert len(matches) == 1
    return matches[0]


def test_derivatives_generator_adjusts_2026_june_holidays() -> None:
    events = generate_derivatives_events(from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))

    a50 = _event_by_kind(events, "sgx_a50_expiry")
    cffex = _event_by_kind(events, "cffex_index_futures_expiry")
    opex = _event_by_kind(events, "us_opex")

    assert a50["event_date"].isoformat() == "2026-06-29"
    assert cffex["event_date"].isoformat() == "2026-06-18"
    assert opex["event_date"].isoformat() == "2026-06-19"
    assert opex["event_time"].isoformat() == "2026-06-18T20:00:00+00:00"


def test_derivatives_generator_skips_uncovered_china_calendar_years() -> None:
    events = generate_derivatives_events(from_date=date(2027, 6, 1), to_date=date(2027, 6, 30))

    kinds = {event.get("event_kind") for event in events}
    assert "us_opex" in kinds
    assert "sgx_a50_expiry" not in kinds
    assert "cffex_index_futures_expiry" not in kinds
