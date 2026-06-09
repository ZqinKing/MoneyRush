from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from collector.services.anomaly_reason_analyzer import AnomalyReasonResult, compute_evidence_fingerprint
from collector.workers import symbol_loop
from collector.workers.symbol_loop import CollectorWorker


def _settings(**overrides):
    defaults = {
        "anomaly_reason_max_attempts": 3,
        "anomaly_reason_retry_cooldown_minutes": 15,
        "anomaly_reason_retry_backoff_minutes": 60,
        "anomaly_post_close_review_enabled": True,
        "anomaly_post_close_batch_size": 10,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _anomaly_row(**overrides) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 101,
        "symbol": "000001",
        "anomaly_date": date(2026, 6, 8),
        "anomaly_type": "price_jump",
        "first_trigger_ts": datetime(2026, 6, 8, 2, 0, tzinfo=UTC),
        "first_trigger_bucket": datetime(2026, 6, 8, 2, 0, tzinfo=UTC),
        "severity": "critical",
        "change_pct": 4.2,
        "volume_ratio": 3.1,
        "event_count": 2,
        "ai_reason_evidence_fingerprint": None,
        "ai_reason_attempt_count": 0,
        "ai_reason_phase": "intraday",
        "post_close_checkpoint_attempt_count": 0,
        "post_close_checkpoint_evidence_fingerprint": None,
        "ai_reason_post_close_evidence_fingerprint": None,
    }
    row.update(overrides)
    return row


def _context(**overrides) -> dict[str, object]:
    value: dict[str, object] = {
        "news": [{"id": 1}],
        "announcements": [],
        "reports": [],
        "market_summary": {"last_price": 10.2},
        "dragon_tiger_daily": None,
        "dragon_tiger_institution": None,
        "dragon_tiger_published_for_date": False,
    }
    value.update(overrides)
    return value


class FakePostgres:
    def __init__(self, *, pending_rows=None, post_close_rows=None, context=None) -> None:
        self.pending_rows = pending_rows or []
        self.post_close_rows = post_close_rows or []
        self.context = context or _context()
        self.audit_rows = []
        self.ai_reason_updates = []
        self.post_close_updates = []
        self.checkpoints = []

    async def fetch_pending_anomaly_reasons(self, *, limit, max_attempts):
        return self.pending_rows

    async def fetch_post_close_review_candidates(self, *, trade_date, limit, max_attempts):
        return self.post_close_rows

    async def fetch_anomaly_reason_context(self, **kwargs):
        return self.context

    async def insert_llm_audit_rows(self, items):
        self.audit_rows.extend(items)

    async def update_anomaly_ai_reasons(self, items):
        self.ai_reason_updates.extend(items)

    async def update_anomaly_post_close_ai_reasons(self, items):
        self.post_close_updates.extend(items)

    async def upsert_post_close_review_checkpoint(self, item):
        self.checkpoints.append(item)


class FakeAnalyzer:
    def __init__(self, result: AnomalyReasonResult | None = None) -> None:
        self.result = result
        self.analyze_calls = 0

    def reason_window(self, trigger_ts, *, phase):
        cutoff_at = trigger_ts + timedelta(minutes=5)
        return trigger_ts - timedelta(minutes=30), cutoff_at, cutoff_at

    def analyze(self, row, context, *, phase, evidence_cutoff_at):
        self.analyze_calls += 1
        if self.result is None:
            raise AssertionError("analyze should not be called")
        return self.result


def _worker(postgres: FakePostgres, analyzer: FakeAnalyzer) -> CollectorWorker:
    worker = CollectorWorker.__new__(CollectorWorker)
    setattr(worker, "_settings", _settings())
    setattr(worker, "_postgres", postgres)
    setattr(worker, "_anomaly_reason_analyzer", analyzer)
    setattr(worker, "_last_anomaly_reason_analysis_at", 0.0)
    setattr(worker, "_last_post_close_reason_analysis_at", 0.0)
    return worker


def test_intraday_unchanged_fingerprint_does_not_write_audit(monkeypatch):
    row = _anomaly_row()
    context = _context()
    cutoff_at = datetime(2026, 6, 8, 2, 5, tzinfo=UTC)
    row["ai_reason_evidence_fingerprint"] = compute_evidence_fingerprint(row, context, phase="intraday", cutoff_at=cutoff_at)
    postgres = FakePostgres(pending_rows=[row], context=context)
    analyzer = FakeAnalyzer()
    worker = _worker(postgres, analyzer)
    monkeypatch.setattr(symbol_loop, "is_ai_configured", lambda settings: True)
    monkeypatch.setattr(symbol_loop, "build_market_status", lambda: ({"state": "open"}, True))

    asyncio.run(worker._analyze_pending_anomaly_reasons(force=True))

    assert analyzer.analyze_calls == 0
    assert postgres.audit_rows == []
    assert postgres.ai_reason_updates == []


def test_post_close_unpublished_dragon_tiger_does_not_write_audit(monkeypatch):
    row = _anomaly_row()
    postgres = FakePostgres(post_close_rows=[row], context=_context())
    analyzer = FakeAnalyzer()
    worker = _worker(postgres, analyzer)
    monkeypatch.setattr(symbol_loop, "is_ai_configured", lambda settings: True)
    monkeypatch.setattr(symbol_loop, "_post_close_review_due", lambda settings, include_dragon_tiger=False: True)
    monkeypatch.setattr(symbol_loop, "_dragon_tiger_evidence_deadline", lambda settings, trade_date: datetime.now(UTC) + timedelta(hours=1))

    asyncio.run(worker._analyze_post_close_anomaly_reasons(force=True))

    assert analyzer.analyze_calls == 0
    assert postgres.audit_rows == []
    assert postgres.checkpoints[-1]["status"] == "pending"
    assert postgres.checkpoints[-1]["last_error"] == "dragon_tiger_not_published_yet"


def test_post_close_unavailable_dragon_tiger_does_not_write_audit(monkeypatch):
    row = _anomaly_row()
    postgres = FakePostgres(post_close_rows=[row], context=_context())
    analyzer = FakeAnalyzer()
    worker = _worker(postgres, analyzer)
    monkeypatch.setattr(symbol_loop, "is_ai_configured", lambda settings: True)
    monkeypatch.setattr(symbol_loop, "_post_close_review_due", lambda settings, include_dragon_tiger=False: True)
    monkeypatch.setattr(symbol_loop, "_dragon_tiger_evidence_deadline", lambda settings, trade_date: datetime.now(UTC) - timedelta(hours=1))

    asyncio.run(worker._analyze_post_close_anomaly_reasons(force=True))

    assert analyzer.analyze_calls == 0
    assert postgres.audit_rows == []
    assert postgres.checkpoints[-1]["status"] == "unavailable"
    assert postgres.checkpoints[-1]["last_error"] == "dragon_tiger_unavailable_after_grace_window"


def test_real_intraday_success_and_failure_write_audit_rows(monkeypatch):
    context = _context(news=[{"id": 2}])
    success = AnomalyReasonResult(
        reason="异动可能与公告进展有关",
        status="completed",
        related_news_ids=[2],
        related_announcement_ids=[],
        llm_succeeded=True,
        attempted=True,
        model_used="test-model",
        prompt_version="test-v1",
        latency_ms=123,
        attempts=[{"status": "completed", "usage": {"total_tokens": 9}}],
        phase="intraday",
        evidence_cutoff_at=datetime(2026, 6, 8, 2, 5, tzinfo=UTC),
        evidence_fingerprint="success-fingerprint",
    )
    failure = AnomalyReasonResult(
        reason=None,
        status="failed",
        related_news_ids=[],
        related_announcement_ids=[],
        llm_succeeded=False,
        attempted=True,
        skip_reason="request_failed",
        model_used="test-model",
        prompt_version="test-v1",
        attempts=[{"status": "failed"}],
        phase="intraday",
        evidence_cutoff_at=datetime(2026, 6, 8, 2, 5, tzinfo=UTC),
        evidence_fingerprint="failure-fingerprint",
    )
    monkeypatch.setattr(symbol_loop, "is_ai_configured", lambda settings: True)
    monkeypatch.setattr(symbol_loop, "build_market_status", lambda: ({"state": "open"}, True))

    success_postgres = FakePostgres(pending_rows=[_anomaly_row(id=201)], context=context)
    asyncio.run(_worker(success_postgres, FakeAnalyzer(success))._analyze_pending_anomaly_reasons(force=True))

    failure_postgres = FakePostgres(pending_rows=[_anomaly_row(id=202)], context=context)
    asyncio.run(_worker(failure_postgres, FakeAnalyzer(failure))._analyze_pending_anomaly_reasons(force=True))

    assert success_postgres.audit_rows[0]["status"] == "completed"
    assert success_postgres.audit_rows[0]["meta"]["llmSucceeded"] is True
    assert success_postgres.audit_rows[0]["meta"]["attempts"] == success.attempts
    assert failure_postgres.audit_rows[0]["status"] == "failed"
    assert failure_postgres.audit_rows[0]["meta"]["skipReason"] == "request_failed"
    assert failure_postgres.audit_rows[0]["meta"]["llmSucceeded"] is False
