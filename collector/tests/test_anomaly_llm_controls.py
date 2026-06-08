from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from collector.services.anomaly_reason_analyzer import compute_evidence_fingerprint
from collector.workers.symbol_loop import _next_retry_at


def _settings(**overrides):
    defaults = {
        "anomaly_reason_max_attempts": 3,
        "anomaly_reason_retry_cooldown_minutes": 15,
        "anomaly_reason_retry_backoff_minutes": 60,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _anomaly():
    return {
        "symbol": "000001",
        "anomaly_date": date(2026, 6, 8),
        "anomaly_type": "price_jump",
        "first_trigger_bucket": datetime(2026, 6, 8, 2, 0, tzinfo=UTC),
        "severity": "critical",
        "change_pct": 4.2,
        "volume_ratio": 3.1,
        "event_count": 2,
    }


def test_evidence_fingerprint_is_stable_for_same_context():
    cutoff_at = datetime(2026, 6, 8, 7, 15, 20, tzinfo=UTC)
    context = {
        "news": [{"id": 2}, {"id": 1}],
        "announcements": [{"dedupe_key": "ann-1"}],
        "reports": [],
        "market_summary": {"last_price": 10.2, "dominant_side": "buy"},
        "dragon_tiger_daily": None,
        "dragon_tiger_institution": None,
    }

    left = compute_evidence_fingerprint(_anomaly(), context, phase="intraday", cutoff_at=cutoff_at)
    right = compute_evidence_fingerprint(_anomaly(), context, phase="intraday", cutoff_at=cutoff_at.replace(second=55))

    assert left == right


def test_evidence_fingerprint_changes_when_new_evidence_appears():
    cutoff_at = datetime(2026, 6, 8, 7, 15, tzinfo=UTC)
    base_context = {"news": [{"id": 1}], "announcements": [], "reports": [], "market_summary": {}}
    changed_context = {"news": [{"id": 1}, {"id": 3}], "announcements": [], "reports": [], "market_summary": {}}

    assert compute_evidence_fingerprint(_anomaly(), base_context, phase="intraday", cutoff_at=cutoff_at) != compute_evidence_fingerprint(_anomaly(), changed_context, phase="intraday", cutoff_at=cutoff_at)


def test_evidence_fingerprint_ignores_date_level_dragon_tiger_publication_flag():
    cutoff_at = datetime(2026, 6, 8, 7, 15, tzinfo=UTC)
    base_context = {"news": [], "announcements": [], "reports": [], "market_summary": {"last_price": 10.2}}
    published_context = {**base_context, "dragon_tiger_published_for_date": True}

    assert compute_evidence_fingerprint(_anomaly(), base_context, phase="post_close", cutoff_at=cutoff_at) == compute_evidence_fingerprint(_anomaly(), published_context, phase="post_close", cutoff_at=cutoff_at)


def test_failed_rows_respect_cooldown_and_max_attempts():
    now = datetime.now(UTC)
    first_retry = _next_retry_at(attempt_count=1, settings=_settings())
    second_retry = _next_retry_at(attempt_count=2, settings=_settings())

    assert first_retry is not None
    assert second_retry is not None
    assert first_retry > now + timedelta(minutes=14)
    assert second_retry > now + timedelta(minutes=59)
    assert _next_retry_at(attempt_count=3, settings=_settings()) is None
