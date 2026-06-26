from __future__ import annotations

from datetime import UTC, datetime

from collector.services.event_calendar_normalizer import normalize_timeline_event


def test_normalized_event_is_idempotent_and_uses_beijing_date() -> None:
    event_time = datetime(2026, 6, 25, 12, 30, tzinfo=UTC)
    first = normalize_timeline_event(
        title="美国 PCE / Personal Income and Outlays 发布",
        category="macro",
        level="high",
        event_kind="us_pce",
        source="official",
        source_provider="bea",
        source_event_id="bea:pce:2026-06-25",
        impact_assets=["USD", "UST"],
        event_time=event_time,
        event_timezone="UTC",
        raw_payload={"releaseDate": event_time},
    )
    second = normalize_timeline_event(
        title="美国 PCE / Personal Income and Outlays 发布",
        category="macro",
        level="high",
        event_kind="us_pce",
        source="official",
        source_provider="bea",
        source_event_id="bea:pce:2026-06-25-revised-id",
        impact_assets=["USD", "UST"],
        event_time=event_time,
        event_timezone="UTC",
        raw_payload={"releaseDate": event_time},
    )

    assert first["id"] == second["id"]
    assert first["duplicate_group_key"] == second["duplicate_group_key"]
    assert first["event_date"].isoformat() == "2026-06-25"
    assert first["raw_payload"] == {"releaseDate": "2026-06-25T12:30:00+00:00"}
