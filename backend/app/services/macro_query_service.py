from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (float, int, Decimal)):
        return float(value)
    return None


def _to_iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _to_date_string(value: object) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


def _decode_jsonish(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


class MacroQueryService:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            await self._ensure_runtime_schema()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ensure_runtime_schema(self) -> None:
        if self._pool is None:
            raise RuntimeError("MacroQueryService must be connected before schema initialization")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_observation (
                    series_id TEXT NOT NULL,
                    observation_date DATE NOT NULL,
                    value NUMERIC(18, 6),
                    source TEXT NOT NULL DEFAULT 'fred',
                    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    PRIMARY KEY (series_id, observation_date)
                )
                """
            )
            await connection.execute("CREATE INDEX IF NOT EXISTS macro_observation_series_date_idx ON macro_observation (series_id, observation_date DESC)")
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_snapshot (
                    snapshot_key TEXT PRIMARY KEY,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_analysis (
                    id BIGSERIAL PRIMARY KEY,
                    trigger_type TEXT NOT NULL,
                    focus TEXT NOT NULL DEFAULT 'general',
                    depth TEXT NOT NULL DEFAULT 'brief',
                    snapshot_date DATE,
                    data_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                    analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'completed',
                    model_used TEXT,
                    prompt_version TEXT NOT NULL DEFAULT 'v1',
                    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    cache_key TEXT
                )
                """
            )
            await connection.execute("CREATE INDEX IF NOT EXISTS macro_analysis_generated_idx ON macro_analysis (generated_at DESC)")
            await connection.execute("CREATE INDEX IF NOT EXISTS macro_analysis_snapshot_idx ON macro_analysis (snapshot_date DESC, trigger_type)")

    async def fetch_snapshot(self, snapshot_key: str = "us_treasury_yields") -> dict[str, object] | None:
        row = await self._fetchrow(
            """
            SELECT updated_at, payload
            FROM macro_snapshot
            WHERE snapshot_key = $1
            """,
            snapshot_key,
        )
        if row is None:
            return None
        payload = _decode_jsonish(row["payload"])
        payload.setdefault("updatedAt", _to_iso(row["updated_at"]))
        return payload

    async def fetch_history(self, series_id: str, *, limit: int = 90) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT series_id, observation_date, value, source, collected_at
            FROM macro_observation
            WHERE series_id = $1
            ORDER BY observation_date DESC
            LIMIT $2
            """,
            series_id,
            max(min(limit, 365), 1),
        )
        return [
            {
                "seriesId": row["series_id"],
                "date": _to_date_string(row["observation_date"]),
                "value": _to_float(row["value"]),
                "source": row["source"],
                "collectedAt": _to_iso(row["collected_at"]),
            }
            for row in rows
        ]

    async def fetch_latest_analysis(self) -> dict[str, object] | None:
        row = await self._fetchrow(
            """
            SELECT id, trigger_type, focus, depth, snapshot_date, data_snapshot, analysis,
                   status, model_used, prompt_version, generated_at, cache_key
            FROM macro_analysis
            ORDER BY generated_at DESC
            LIMIT 1
            """
        )
        return self._serialize_analysis(row) if row is not None else None

    async def insert_analysis(
        self,
        *,
        trigger_type: str,
        focus: str,
        depth: str,
        snapshot_date: str | None,
        data_snapshot: dict[str, object],
        analysis: dict[str, object],
        status: str = "completed",
        model_used: str | None = None,
        prompt_version: str = "rules-v1",
        cache_key: str | None = None,
    ) -> dict[str, object]:
        parsed_snapshot_date = date.fromisoformat(snapshot_date) if snapshot_date else None
        row = await self._fetchrow(
            """
            INSERT INTO macro_analysis (
                trigger_type, focus, depth, snapshot_date, data_snapshot, analysis,
                status, model_used, prompt_version, cache_key
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10)
            RETURNING id, trigger_type, focus, depth, snapshot_date, data_snapshot, analysis,
                      status, model_used, prompt_version, generated_at, cache_key
            """,
            trigger_type,
            focus,
            depth,
            parsed_snapshot_date,
            json.dumps(data_snapshot, default=str),
            json.dumps(analysis, default=str),
            status,
            model_used,
            prompt_version,
            cache_key,
        )
        if row is None:
            raise RuntimeError("macro analysis insert did not return a row")
        return self._serialize_analysis(row)

    async def _fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("MacroQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return list(await connection.fetch(query, *args))

    async def _fetchrow(self, query: str, *args: object) -> asyncpg.Record | None:
        if self._pool is None:
            raise RuntimeError("MacroQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetchrow(query, *args)

    @staticmethod
    def _serialize_analysis(row: asyncpg.Record) -> dict[str, object]:
        return {
            "id": row["id"],
            "triggerType": row["trigger_type"],
            "focus": row["focus"],
            "depth": row["depth"],
            "snapshotDate": _to_date_string(row["snapshot_date"]),
            "dataSnapshot": _decode_jsonish(row["data_snapshot"]),
            "analysis": _decode_jsonish(row["analysis"]),
            "status": row["status"],
            "modelUsed": row["model_used"],
            "promptVersion": row["prompt_version"],
            "generatedAt": _to_iso(row["generated_at"]),
            "cacheKey": row["cache_key"],
        }
