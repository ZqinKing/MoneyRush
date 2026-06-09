from __future__ import annotations

import json
from datetime import UTC, date, datetime

import asyncpg


DEFAULT_AUDIT_LOG_LIMIT = 50
MAX_AUDIT_LOG_LIMIT = 200


def _to_iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _decode_jsonish(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(source: dict[str, object], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _to_int(source.get(key))
        if value is not None:
            return value
    return None


def _first_present_int(values: tuple[int | None, ...]) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None


def _latest_attempt(meta: dict[str, object]) -> dict[str, object]:
    attempts = meta.get("attempts")
    if not isinstance(attempts, list):
        return {}
    for item in reversed(attempts):
        if isinstance(item, dict):
            return item
    return {}


def _extract_usage_tokens(meta: dict[str, object]) -> tuple[int | None, int | None, int | None]:
    latest_attempt = _latest_attempt(meta)
    usage = latest_attempt.get("usage") if isinstance(latest_attempt.get("usage"), dict) else meta.get("usage")
    usage_map = usage if isinstance(usage, dict) else {}
    input_tokens = _first_present_int((
        _first_int(meta, ("inputTokens", "promptTokens", "prompt_tokens")),
        _first_int(latest_attempt, ("inputTokens", "promptTokens", "prompt_tokens")),
        _first_int(usage_map, ("inputTokens", "promptTokens", "prompt_tokens", "input_tokens")),
    ))
    output_tokens = _first_present_int((
        _first_int(meta, ("outputTokens", "completionTokens", "completion_tokens")),
        _first_int(latest_attempt, ("outputTokens", "completionTokens", "completion_tokens")),
        _first_int(usage_map, ("outputTokens", "completionTokens", "completion_tokens", "output_tokens")),
    ))
    total_tokens = _first_present_int((
        _first_int(meta, ("totalTokens", "total_tokens")),
        _first_int(latest_attempt, ("totalTokens", "total_tokens")),
        _first_int(usage_map, ("totalTokens", "total_tokens")),
    ))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def _compact_meta_hints(meta: dict[str, object]) -> dict[str, object]:
    hints: dict[str, object] = {}
    for key in ("symbol", "scope", "focus", "depth", "anomalyType", "phase", "fallbackReason", "snapshotDate", "portfolioStatus"):
        value = meta.get(key)
        if value is not None and value != "":
            hints[key] = value
    return hints


def _serialize_log_row(row: asyncpg.Record) -> dict[str, object]:
    meta = _decode_jsonish(row["meta"])
    latest_attempt = _latest_attempt(meta)
    input_tokens, output_tokens, total_tokens = _extract_usage_tokens(meta)
    model_used = row["model_used"] or latest_attempt.get("model")
    return {
        "id": int(row["id"]),
        "invokedAt": _to_iso(row["invoked_at"]),
        "auditDate": row["audit_date"].isoformat() if isinstance(row["audit_date"], date) else str(row["audit_date"]),
        "menuModule": row["menu_module"],
        "callCategory": row["call_category"],
        "status": row["status"],
        "modelUsed": str(model_used) if model_used else None,
        "promptVersion": row["prompt_version"],
        "latencyMs": _to_int(row["latency_ms"]),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "skipReason": meta.get("skipReason") or meta.get("fallbackReason"),
        "attemptCount": _to_int(meta.get("attemptCount") or latest_attempt.get("attempt")),
        "llmSucceeded": meta.get("llmSucceeded"),
        "attemptStatus": latest_attempt.get("status"),
        "statusCode": _to_int(latest_attempt.get("statusCode")),
        "finishReason": latest_attempt.get("finishReason"),
        "symbol": meta.get("symbol"),
        "scope": meta.get("scope"),
        "metaHints": _compact_meta_hints(meta),
    }


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

    async def fetch_daily_summary(
        self,
        target_date: date,
        *,
        limit: int = DEFAULT_AUDIT_LOG_LIMIT,
        offset: int = 0,
    ) -> dict[str, object]:
        if self._pool is None:
            raise RuntimeError("LlmAuditQueryService must be connected before use")
        log_limit = min(max(int(limit), 1), MAX_AUDIT_LOG_LIMIT)
        log_offset = max(int(offset), 0)

        async with self._pool.acquire() as connection:
            total_row = await connection.fetchrow(
                """
                SELECT COUNT(*) AS total_count, MAX(invoked_at) AS latest_invoked_at
                FROM llm_invocation_audit
                WHERE audit_date = $1
                  AND status <> 'skipped'
                """,
                target_date,
            )
            module_rows = await connection.fetch(
                """
                SELECT menu_module, COUNT(*) AS item_count
                FROM llm_invocation_audit
                WHERE audit_date = $1
                  AND status <> 'skipped'
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
                  AND status <> 'skipped'
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
                  AND status <> 'skipped'
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
                  AND status <> 'skipped'
                GROUP BY menu_module, call_category
                ORDER BY menu_module ASC, call_category ASC
                """,
                target_date,
            )
            log_rows = await connection.fetch(
                """
                SELECT id, invoked_at, audit_date, menu_module, call_category, status,
                       model_used, prompt_version, latency_ms, meta
                FROM llm_invocation_audit
                WHERE audit_date = $1
                  AND status <> 'skipped'
                ORDER BY invoked_at DESC, id DESC
                LIMIT $2
                OFFSET $3
                """,
                target_date,
                log_limit,
                log_offset,
            )

        total_count = int(total_row["total_count"] or 0) if total_row is not None else 0
        latest_invoked_at = _to_iso(total_row["latest_invoked_at"]) if total_row is not None else None
        next_offset = log_offset + len(log_rows)
        has_more = next_offset < total_count

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
            "items": [_serialize_log_row(row) for row in log_rows],
            "limit": log_limit,
            "offset": log_offset,
            "nextOffset": next_offset if has_more else None,
            "hasMore": has_more,
        }
