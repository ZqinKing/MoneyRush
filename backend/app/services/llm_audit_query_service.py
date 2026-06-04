from __future__ import annotations

import json
from datetime import UTC, date, datetime

import asyncpg


def _to_iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


class LlmAuditQueryService:
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
            raise RuntimeError("LlmAuditQueryService must be connected before schema initialization")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_invocation_audit (
                    id BIGSERIAL PRIMARY KEY,
                    invoked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    audit_date DATE NOT NULL,
                    menu_module TEXT NOT NULL,
                    call_category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model_used TEXT,
                    prompt_version TEXT,
                    latency_ms INTEGER,
                    meta JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS llm_invocation_audit_date_module_category_idx ON llm_invocation_audit (audit_date DESC, menu_module, call_category)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS llm_invocation_audit_status_date_idx ON llm_invocation_audit (status, audit_date DESC)"
            )

    async def insert_audit_rows(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("LlmAuditQueryService must be connected before use")
        if not items:
            return

        rows = [
            (
                item["invoked_at"],
                item["audit_date"],
                item["menu_module"],
                item["call_category"],
                item["status"],
                item.get("model_used"),
                item.get("prompt_version"),
                item.get("latency_ms"),
                json.dumps(item.get("meta", {}), default=str),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO llm_invocation_audit (
                    invoked_at,
                    audit_date,
                    menu_module,
                    call_category,
                    status,
                    model_used,
                    prompt_version,
                    latency_ms,
                    meta
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                """,
                rows,
            )

    async def fetch_daily_summary(self, target_date: date) -> dict[str, object]:
        if self._pool is None:
            raise RuntimeError("LlmAuditQueryService must be connected before use")

        async with self._pool.acquire() as connection:
            total_row = await connection.fetchrow(
                """
                SELECT COUNT(*) AS total_count, MAX(invoked_at) AS latest_invoked_at
                FROM llm_invocation_audit
                WHERE audit_date = $1
                """,
                target_date,
            )
            module_rows = await connection.fetch(
                """
                SELECT menu_module, COUNT(*) AS item_count
                FROM llm_invocation_audit
                WHERE audit_date = $1
                GROUP BY menu_module
                ORDER BY item_count DESC, menu_module ASC
                """,
                target_date,
            )
            category_rows = await connection.fetch(
                """
                SELECT call_category, COUNT(*) AS item_count
                FROM llm_invocation_audit
                WHERE audit_date = $1
                GROUP BY call_category
                ORDER BY item_count DESC, call_category ASC
                """,
                target_date,
            )
            status_rows = await connection.fetch(
                """
                SELECT status, COUNT(*) AS item_count
                FROM llm_invocation_audit
                WHERE audit_date = $1
                GROUP BY status
                ORDER BY item_count DESC, status ASC
                """,
                target_date,
            )
            matrix_rows = await connection.fetch(
                """
                SELECT menu_module, call_category, COUNT(*) AS item_count
                FROM llm_invocation_audit
                WHERE audit_date = $1
                GROUP BY menu_module, call_category
                ORDER BY menu_module ASC, call_category ASC
                """,
                target_date,
            )

        total_count = int(total_row["total_count"] or 0) if total_row is not None else 0
        latest_invoked_at = _to_iso(total_row["latest_invoked_at"]) if total_row is not None else None

        return {
            "date": target_date.isoformat(),
            "totalCount": total_count,
            "latestInvokedAt": latest_invoked_at,
            "byModule": [
                {"menuModule": row["menu_module"], "count": int(row["item_count"] or 0)}
                for row in module_rows
            ],
            "byCategory": [
                {"callCategory": row["call_category"], "count": int(row["item_count"] or 0)}
                for row in category_rows
            ],
            "byStatus": [
                {"status": row["status"], "count": int(row["item_count"] or 0)}
                for row in status_rows
            ],
            "byModuleAndCategory": [
                {
                    "menuModule": row["menu_module"],
                    "callCategory": row["call_category"],
                    "count": int(row["item_count"] or 0),
                }
                for row in matrix_rows
            ],
        }
