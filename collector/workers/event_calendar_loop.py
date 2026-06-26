from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime

from redis.asyncio import Redis

from collector.services.derivatives_calendar import generate_derivatives_events
from collector.services.event_calendar_window import build_event_calendar_window
from collector.services.official_event_calendar import EventCalendarSourceError, OfficialEventCalendarClient, load_bls_fixture_events, load_fomc_fixture_events
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)


class EventCalendarCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = OfficialEventCalendarClient(timeout_seconds=settings.event_calendar_request_timeout_seconds)
        self._postgres_ready = False

    async def run(self) -> None:
        if not self._settings.event_calendar_collector_enabled:
            await self._set_collector_status(status="disabled", reason="config_disabled")
            logger.info("event calendar collector disabled by config")
            return
        logger.info("event calendar collector started")
        while True:
            await self.run_once()
            await asyncio.sleep(max(float(self._settings.event_calendar_refresh_seconds), 3600.0))

    async def run_once(self) -> dict[str, object]:
        if not self._settings.event_calendar_collector_enabled:
            payload = await self._set_collector_status(status="disabled", reason="config_disabled")
            return payload

        try:
            await self._ensure_postgres_connection()
            result = await self._refresh_event_calendar()
        except Exception:
            self._postgres_ready = False
            logger.exception("event calendar refresh failed")
            return await self._set_collector_status(status="error", reason="event_calendar_refresh_error")
        return result

    async def close(self) -> None:
        await self._postgres.close()
        await self._redis.aclose()

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect_timeline_only()
        self._postgres_ready = True

    async def _refresh_event_calendar(self) -> dict[str, object]:
        today = date.today()
        from_date, to_date, coverage_warnings = build_event_calendar_window(
            today=today,
            lookback_days=int(self._settings.event_calendar_lookback_days),
            lookahead_days=int(self._settings.event_calendar_lookahead_days),
        )
        source_errors: list[dict[str, object]] = []
        events: list[dict[str, object]] = []

        for provider, fetcher in (
            ("bea", lambda: self._client.fetch_bea_events(from_date=from_date, to_date=to_date, include_gdp=True)),
            ("bls", lambda: self._client.fetch_bls_events(from_date=from_date, to_date=to_date)),
        ):
            try:
                rows = await asyncio.to_thread(fetcher)
            except EventCalendarSourceError as exc:
                error_payload: dict[str, object] = {"provider": provider, "reason": exc.reason, "statusCode": exc.status_code}
                if provider == "bls":
                    rows = load_bls_fixture_events(from_date=from_date, to_date=to_date)
                    error_payload["fallback"] = "bls_official_fixture"
                else:
                    rows = []
                source_errors.append(error_payload)
                logger.warning("event calendar source failed", extra={"provider": provider, "reason": exc.reason, "status_code": exc.status_code})
            events.extend(rows)

        events.extend(load_fomc_fixture_events(from_date=from_date, to_date=to_date))
        events.extend(generate_derivatives_events(from_date=from_date, to_date=to_date))

        upserted = await self._postgres.upsert_timeline_events(events)
        status = "degraded" if source_errors or coverage_warnings else "success"
        reason = "partial_source_failure" if source_errors else "calendar_fixture_horizon_limited" if coverage_warnings else None
        return await self._set_collector_status(
            status=status,
            reason=reason,
            extra={
                "eventCount": len(events),
                "upsertedCount": upserted,
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
                "coverageWarnings": coverage_warnings,
                "sourceErrors": source_errors,
            },
        )

    async def _set_collector_status(self, *, status: str, reason: str | None, extra: dict[str, object] | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": status,
            "reason": reason,
            "updatedAt": datetime.now(UTC).isoformat(),
            "source": "event-calendar-collector",
        }
        if extra:
            payload.update(extra)
        await self._redis.set(self._settings.event_calendar_status_cache_key, json.dumps(payload, default=str, ensure_ascii=False))
        return payload
