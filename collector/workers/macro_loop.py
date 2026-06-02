from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from collector.services.fred_macro_client import FredMacroClient, FredMacroClientError
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)

FRED_SERIES = ("DGS2", "DGS10", "DGS30", "T10Y2Y", "VIXCLS", "DTWEXBGS", "SP500")
SNAPSHOT_KEY = "us_treasury_yields"


class MacroCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = FredMacroClient(settings)
        self._postgres_ready = False

    async def run(self) -> None:
        if not self._settings.macro_collector_enabled or not self._settings.macro_monitor_enabled:
            await self._set_collector_status(status="disabled", reason="config_disabled")
            logger.info("macro collector disabled by config")
            return

        if not self._settings.fred_api_key:
            await self._set_collector_status(status="disabled", reason="missing_fred_api_key")
            logger.info("macro collector disabled because FRED_API_KEY is missing")
            return

        logger.info("macro collector started")
        while True:
            try:
                await self._ensure_postgres_connection()
                await self._refresh_macro_data()
            except FredMacroClientError as exc:
                self._postgres_ready = False
                logger.warning(
                    "macro collector FRED refresh failed",
                    extra={"reason": exc.reason, "series": exc.series_id, "status_code": exc.status_code},
                )
                await self._set_collector_status(status="error", reason=exc.reason)
                await asyncio.sleep(max(float(self._settings.macro_fred_failure_cooldown_seconds), 60.0))
                continue
            except Exception:
                self._postgres_ready = False
                logger.exception("macro collector refresh failed")
                await self._set_collector_status(status="error", reason="macro_refresh_error")
                await asyncio.sleep(max(float(self._settings.macro_fred_failure_cooldown_seconds), 60.0))
                continue

            await asyncio.sleep(max(float(self._settings.macro_collector_refresh_seconds), 300.0))

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect()
        self._postgres_ready = True

    async def _refresh_macro_data(self) -> None:
        observations_by_series: dict[str, list[dict[str, object]]] = {}
        series_errors: list[dict[str, object]] = []
        for series_id in FRED_SERIES:
            try:
                rows = await asyncio.to_thread(
                    self._client.fetch_series,
                    series_id,
                    lookback_days=self._settings.macro_fred_observation_lookback_days,
                )
            except FredMacroClientError as exc:
                rows = []
                series_errors.append({"seriesId": series_id, "reason": exc.reason, "statusCode": exc.status_code})
                logger.warning(
                    "macro collector skipped failed FRED series",
                    extra={"series": series_id, "reason": exc.reason, "status_code": exc.status_code},
                )
            observations_by_series[series_id] = rows
            await self._postgres.upsert_macro_observations(rows)

        snapshot = self._build_snapshot(observations_by_series, series_errors=series_errors)
        await self._postgres.upsert_macro_snapshot(snapshot_key=SNAPSHOT_KEY, payload=snapshot)
        await self._redis.set(self._settings.macro_snapshot_cache_key, json.dumps(snapshot, default=str))
        status = "degraded" if series_errors else "success"
        reason = "partial_series_failure" if series_errors else None
        await self._set_collector_status(
            status=status,
            reason=reason,
            extra={"seriesCount": len(observations_by_series), "seriesErrors": series_errors},
        )

    def _build_snapshot(
        self,
        observations_by_series: dict[str, list[dict[str, object]]],
        *,
        series_errors: list[dict[str, object]],
    ) -> dict[str, object]:
        generated_at = datetime.now(UTC).isoformat()
        y2 = self._build_yield_metric(observations_by_series.get("DGS2", []))
        y10 = self._build_yield_metric(observations_by_series.get("DGS10", []))
        y30 = self._build_yield_metric(observations_by_series.get("DGS30", []))
        spread = self._build_yield_metric(observations_by_series.get("T10Y2Y", []))
        context = {
            "vix": self._build_level_metric(observations_by_series.get("VIXCLS", [])),
            "dxy": self._build_level_metric(observations_by_series.get("DTWEXBGS", [])),
            "sp500": self._build_level_metric(observations_by_series.get("SP500", []), pct_change=True),
        }
        latest_date = self._latest_date(y2, y10, y30)
        alerts = []
        y10_value = y10.get("value")
        threshold = float(self._settings.macro_ten_year_warning_threshold)
        if isinstance(y10_value, (int, float)) and y10_value >= threshold:
            alerts.append(
                {
                    "type": "threshold",
                    "series": "DGS10",
                    "level": "warning",
                    "message": f"10Y 美债已达到 {y10_value:.2f}%，高于 {threshold:.2f}% 关注线。",
                }
            )
        return {
            "date": latest_date,
            "source": "fred",
            "updatedAt": generated_at,
            "yields": {
                "y2": y2,
                "y10": y10,
                "y30": y30,
                "spread10Y2YBp": self._percent_to_basis_points(spread.get("value")),
            },
            "context": context,
            "seriesErrors": series_errors,
            "alerts": alerts,
            "status": "degraded" if series_errors and latest_date else "fresh" if latest_date else "unavailable",
        }

    @staticmethod
    def _valid_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        return [row for row in rows if isinstance(row.get("value"), (int, float))]

    def _build_yield_metric(self, rows: list[dict[str, object]]) -> dict[str, object]:
        valid_rows = self._valid_rows(rows)
        if not valid_rows:
            return {"value": None, "date": None, "changeD1Bp": None, "changeD5Bp": None, "changeD20Bp": None}
        current = valid_rows[0]
        current_value = float(current["value"])
        return {
            "value": current_value,
            "date": current["observation_date"].isoformat(),
            "changeD1Bp": self._change_bp(valid_rows, 1, current_value),
            "changeD5Bp": self._change_bp(valid_rows, 5, current_value),
            "changeD20Bp": self._change_bp(valid_rows, 20, current_value),
        }

    def _build_level_metric(self, rows: list[dict[str, object]], *, pct_change: bool = False) -> dict[str, object]:
        valid_rows = self._valid_rows(rows)
        if not valid_rows:
            return {"value": None, "date": None, "changeD1Pct" if pct_change else "changeD1": None}
        current = valid_rows[0]
        current_value = float(current["value"])
        previous_value = float(valid_rows[1]["value"]) if len(valid_rows) > 1 else None
        change = None
        if previous_value is not None:
            change = ((current_value / previous_value) - 1) * 100 if pct_change and previous_value else current_value - previous_value
        return {
            "value": current_value,
            "date": current["observation_date"].isoformat(),
            "changeD1Pct" if pct_change else "changeD1": round(change, 4) if isinstance(change, (int, float)) else None,
        }

    @staticmethod
    def _change_bp(rows: list[dict[str, object]], offset: int, current_value: float) -> float | None:
        if len(rows) <= offset:
            return None
        previous = rows[offset].get("value")
        if not isinstance(previous, (int, float)):
            return None
        return round((current_value - float(previous)) * 100, 2)

    @staticmethod
    def _percent_to_basis_points(value: object) -> float | None:
        if not isinstance(value, (int, float)):
            return None
        return round(float(value) * 100, 2)

    @staticmethod
    def _latest_date(*metrics: dict[str, object]) -> str | None:
        dates = [metric.get("date") for metric in metrics if isinstance(metric.get("date"), str)]
        return max(dates) if dates else None

    async def _set_collector_status(self, *, status: str, reason: str | None, extra: dict[str, object] | None = None) -> None:
        payload: dict[str, object] = {
            "status": status,
            "reason": reason,
            "updatedAt": datetime.now(UTC).isoformat(),
            "source": "macro-collector",
        }
        if extra:
            payload.update(extra)
        await self._redis.set(self._settings.macro_collector_status_cache_key, json.dumps(payload, default=str))
