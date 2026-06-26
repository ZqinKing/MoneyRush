from __future__ import annotations

from datetime import date, timedelta


EVENT_CALENDAR_FIXTURE_SUPPORTED_UNTIL = date(2026, 12, 31)


def build_event_calendar_window(
    *,
    today: date,
    lookback_days: int,
    lookahead_days: int,
) -> tuple[date, date, list[dict[str, object]]]:
    from_date = today - timedelta(days=max(int(lookback_days), 0))
    requested_to_date = today + timedelta(days=max(int(lookahead_days), 1))
    warnings: list[dict[str, object]] = []
    to_date = requested_to_date
    if requested_to_date > EVENT_CALENDAR_FIXTURE_SUPPORTED_UNTIL:
        to_date = EVENT_CALENDAR_FIXTURE_SUPPORTED_UNTIL
        warnings.append(
            {
                "reason": "calendar_fixture_horizon_limited",
                "requestedToDate": requested_to_date.isoformat(),
                "effectiveToDate": to_date.isoformat(),
                "supportedUntil": EVENT_CALENDAR_FIXTURE_SUPPORTED_UNTIL.isoformat(),
                "affectedEventKinds": ["fomc_meeting_window", "sgx_a50_expiry", "cffex_index_futures_expiry", "us_cpi", "us_nfp"],
            }
        )
    return from_date, to_date, warnings
