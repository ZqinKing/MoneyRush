from __future__ import annotations

from datetime import date, datetime
from typing import cast

from collector.services.event_calendar_window import build_event_calendar_window
from collector.services.official_event_calendar import load_bls_fixture_events, load_fomc_fixture_events, parse_bea_release_dates, parse_bls_ics


def test_parse_bea_release_dates_maps_pce_and_dedupes_repeated_dates() -> None:
    payload = {
        "Personal Income and Outlays": {
            "release_dates": [
                "2026-06-25T12:30:00+00:00",
                "2026-06-25T12:30:00+00:00",
                "2026-07-30T12:30:00+00:00",
            ]
        },
        "Gross Domestic Product": {"release_dates": ["2026-06-25T12:30:00+00:00"]},
        "file_last_updated": "2026-05-12T14:42:41.381600",
    }

    events = parse_bea_release_dates(cast(dict[str, object], payload), from_date=date(2026, 6, 1), to_date=date(2026, 6, 30), include_gdp=True)

    assert [event["event_kind"] for event in events] == ["us_pce", "us_gdp"]
    assert events[0]["source_provider"] == "bea"
    assert cast(datetime, events[0]["event_time"]).isoformat() == "2026-06-25T12:30:00+00:00"
    assert cast(date, events[0]["event_date"]).isoformat() == "2026-06-25"


def test_parse_bls_ics_handles_us_eastern_dst_and_folded_lines() -> None:
    text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:cpi-2026
DTSTART;TZID=US-Eastern:20260611T083000
SUMMARY:Consumer Price 
 Index
CATEGORIES:IMPORTANT, BLS
END:VEVENT
BEGIN:VEVENT
UID:nfp-2026
DTSTART;TZID=US-Eastern:20251216T083000
SUMMARY:Employment Situation
CATEGORIES:IMPORTANT, BLS
END:VEVENT
END:VCALENDAR
"""

    events = parse_bls_ics(text, from_date=date(2025, 12, 1), to_date=date(2026, 6, 30))

    assert [event["event_kind"] for event in events] == ["us_cpi", "us_nfp"]
    assert cast(datetime, events[0]["event_time"]).isoformat() == "2026-06-11T12:30:00+00:00"
    assert cast(datetime, events[1]["event_time"]).isoformat() == "2025-12-16T13:30:00+00:00"
    assert events[0]["source_event_id"] == "bls:cpi-2026"


def test_fomc_fixture_loads_2026_meeting_windows_without_precise_time() -> None:
    events = load_fomc_fixture_events(from_date=date(2026, 6, 1), to_date=date(2026, 6, 30))

    assert [event["source_provider"] for event in events] == ["fed-fomc-fixture"]
    assert events[0]["event_time"] is None
    assert cast(date, events[0]["event_date"]).isoformat() == "2026-06-16"
    assert cast(date, events[0]["end_date"]).isoformat() == "2026-06-17"


def test_bls_fixture_covers_2026_cpi_and_employment_situation() -> None:
    events = load_bls_fixture_events(from_date=date(2026, 7, 1), to_date=date(2026, 7, 31))

    assert [event["event_kind"] for event in events] == ["us_cpi", "us_nfp"]
    assert cast(datetime, events[0]["event_time"]).isoformat() == "2026-07-14T12:30:00+00:00"
    assert cast(datetime, events[1]["event_time"]).isoformat() == "2026-07-02T12:30:00+00:00"
    assert all(event["source_provider"] == "bls" for event in events)


def test_event_calendar_window_reports_fixture_horizon_limit() -> None:
    from_date, to_date, warnings = build_event_calendar_window(today=date(2026, 6, 26), lookback_days=45, lookahead_days=370)

    assert from_date == date(2026, 5, 12)
    assert to_date == date(2026, 12, 31)
    assert warnings == [
        {
            "reason": "calendar_fixture_horizon_limited",
            "requestedToDate": "2027-07-01",
            "effectiveToDate": "2026-12-31",
            "supportedUntil": "2026-12-31",
            "affectedEventKinds": ["fomc_meeting_window", "sgx_a50_expiry", "cffex_index_futures_expiry", "us_cpi", "us_nfp"],
        }
    ]
