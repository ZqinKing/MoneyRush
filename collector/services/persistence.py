from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import asyncpg


logger = logging.getLogger(__name__)


def _json_dumps(value: object) -> str:
    return json.dumps(value, default=str)


def _coerce_optional_utc_datetime(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _coerce_utc_datetime(value, field_name=field_name)


def _coerce_utc_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)


def _normalize_kline_bucket_ts(bucket_ts: object, period: object) -> datetime:
    normalized_bucket_ts = _coerce_utc_datetime(bucket_ts, field_name="kline.bucketTs")
    if period == "1d":
        return normalized_bucket_ts.replace(hour=0, minute=0, second=0, microsecond=0)

    return normalized_bucket_ts


class PostgresStore:
    def __init__(self, dsn: str, *, enable_runtime_data_repair: bool = False) -> None:
        self._dsn = dsn
        self._enable_runtime_data_repair = enable_runtime_data_repair
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            await self._ensure_runtime_schema()

    async def _ensure_runtime_schema(self) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before schema initialization")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_event (
                    ts TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "SELECT create_hypertable('stock_event', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_event_symbol_ts_idx ON stock_event (symbol, ts DESC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_command_log (
                    ts TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "SELECT create_hypertable('symbol_command_log', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS symbol_command_log_symbol_ts_idx ON symbol_command_log (symbol, ts DESC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_research_report (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    rating TEXT,
                    institution TEXT,
                    analyst TEXT,
                    industry TEXT,
                    published_at TIMESTAMPTZ,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    source_url TEXT,
                    provider TEXT NOT NULL DEFAULT 'akshare',
                    upstream_source TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_research_report_symbol_published_idx ON stock_research_report (symbol, published_at DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_research_report_symbol_first_seen_idx ON stock_research_report (symbol, first_seen_at DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_research_report_published_idx ON stock_research_report (published_at DESC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_news_item (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT,
                    scope TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    content TEXT,
                    article_source TEXT,
                    published_at TIMESTAMPTZ,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    source_url TEXT,
                    provider TEXT NOT NULL DEFAULT 'akshare',
                    upstream_source TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_news_item_scope_published_idx ON stock_news_item (scope, published_at DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_news_item_symbol_published_idx ON stock_news_item (symbol, published_at DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_news_item_symbol_first_seen_idx ON stock_news_item (symbol, first_seen_at DESC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_announcement_item (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    announcement_type TEXT,
                    published_at TIMESTAMPTZ,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    pdf_url TEXT,
                    provider TEXT NOT NULL DEFAULT 'akshare',
                    upstream_source TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_announcement_item_symbol_published_idx ON stock_announcement_item (symbol, published_at DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS stock_announcement_item_symbol_first_seen_idx ON stock_announcement_item (symbol, first_seen_at DESC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS content_fetch_checkpoint (
                    lane TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    cursor JSONB NOT NULL DEFAULT '{}'::jsonb,
                    next_due_at TIMESTAMPTZ NOT NULL,
                    cooldown_until TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_attempt_at TIMESTAMPTZ,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    PRIMARY KEY (lane, symbol)
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS content_fetch_checkpoint_next_due_idx ON content_fetch_checkpoint (next_due_at ASC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS content_fetch_log (
                    id BIGSERIAL PRIMARY KEY,
                    lane TEXT NOT NULL,
                    symbol TEXT,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ NOT NULL,
                    http_hint TEXT,
                    error_message TEXT,
                    meta JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS content_fetch_log_lane_started_idx ON content_fetch_log (lane, started_at DESC)"
            )
            if self._enable_runtime_data_repair:
                repairs = await self._repair_runtime_data(connection)
                if any(repairs.values()):
                    logger.warning("collector repaired persisted market data", extra=repairs)

    async def _repair_runtime_data(self, connection: asyncpg.Connection) -> dict[str, int]:
        repaired_ticks = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_tick
                SET ts = ts - INTERVAL '8 hours'
                WHERE source = 'tencent-finance'
                  AND ts > now() + INTERVAL '1 hour'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_events = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_event
                SET ts = ts - INTERVAL '8 hours'
                WHERE source = 'tencent-finance'
                  AND ts > now() + INTERVAL '1 hour'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_snapshots = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_snapshot
                SET updated_at = updated_at - INTERVAL '8 hours'
                WHERE payload ->> 'source' = 'tencent-finance'
                  AND updated_at > now() + INTERVAL '1 hour'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_profiles = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_profile AS profile
                SET updated_at = profile.updated_at - INTERVAL '8 hours'
                WHERE profile.updated_at > now() + INTERVAL '1 hour'
                  AND EXISTS (
                      SELECT 1
                      FROM stock_snapshot AS snapshot
                      WHERE snapshot.symbol = profile.symbol
                        AND snapshot.payload ->> 'source' = 'tencent-finance'
                  )
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        deleted_invalid_daily_klines = await connection.fetchval(
            """
            WITH deleted AS (
                DELETE FROM stock_kline
                WHERE source = 'tencent-finance'
                  AND period = '1d'
                  AND bucket_ts <> date_trunc('day', bucket_ts)
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM deleted
            """
        )
        repaired_mootdx_tick_volumes = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_tick
                SET volume = volume * 100,
                    raw = jsonb_set(
                        jsonb_set(COALESCE(raw, '{}'::jsonb), '{providerVolumeUnit}', '"lots"', true),
                        '{volumeUnit}',
                        '"shares"',
                        true
                    )
                WHERE source = 'mootdx'
                  AND volume IS NOT NULL
                  AND COALESCE(raw ->> 'volumeUnit', '') <> 'shares'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_mootdx_kline_volumes = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_kline
                SET volume = volume * 100,
                    raw = jsonb_set(
                        jsonb_set(COALESCE(raw, '{}'::jsonb), '{providerVolumeUnit}', '"lots"', true),
                        '{volumeUnit}',
                        '"shares"',
                        true
                    )
                WHERE source = 'mootdx'
                  AND volume IS NOT NULL
                  AND COALESCE(raw ->> 'volumeUnit', '') <> 'shares'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_tencent_tick_volumes = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_tick
                SET volume = volume * 100,
                    raw = jsonb_set(
                        jsonb_set(COALESCE(raw, '{}'::jsonb), '{providerVolumeUnit}', '"lots"', true),
                        '{volumeUnit}',
                        '"shares"',
                        true
                    )
                WHERE source = 'tencent-finance'
                  AND volume IS NOT NULL
                  AND COALESCE(raw ->> 'volumeUnit', '') <> 'shares'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_tencent_kline_volumes = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_kline
                SET volume = volume * 100,
                    raw = jsonb_set(
                        jsonb_set(COALESCE(raw, '{}'::jsonb), '{providerVolumeUnit}', '"lots"', true),
                        '{volumeUnit}',
                        '"shares"',
                        true
                    )
                WHERE source = 'tencent-finance'
                  AND volume IS NOT NULL
                  AND COALESCE(raw ->> 'volumeUnit', '') <> 'shares'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_mootdx_event_volumes = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_event
                SET payload = jsonb_set(
                    jsonb_set(
                        jsonb_set(COALESCE(payload, '{}'::jsonb), '{tick,volume}', to_jsonb((((payload -> 'tick' ->> 'volume')::numeric) * 100)::bigint), true),
                        '{tick,volumeUnit}',
                        '"shares"',
                        true
                    ),
                    '{providerVolumeUnit}',
                    '"lots"',
                    true
                )
                WHERE source IN ('mootdx', 'mootdx+tencent-finance')
                  AND jsonb_typeof(payload -> 'tick') = 'object'
                  AND jsonb_typeof(payload -> 'tick' -> 'volume') = 'number'
                  AND COALESCE(payload -> 'tick' ->> 'volumeUnit', '') <> 'shares'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )
        repaired_tencent_event_volumes = await connection.fetchval(
            """
            WITH repaired AS (
                UPDATE stock_event
                SET payload = jsonb_set(
                    jsonb_set(
                        jsonb_set(COALESCE(payload, '{}'::jsonb), '{tick,volume}', to_jsonb((((payload -> 'tick' ->> 'volume')::numeric) * 100)::bigint), true),
                        '{tick,volumeUnit}',
                        '"shares"',
                        true
                    ),
                    '{providerVolumeUnit}',
                    '"lots"',
                    true
                )
                WHERE source = 'tencent-finance'
                  AND jsonb_typeof(payload -> 'tick') = 'object'
                  AND jsonb_typeof(payload -> 'tick' -> 'volume') = 'number'
                  AND COALESCE(payload -> 'tick' ->> 'volumeUnit', '') <> 'shares'
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM repaired
            """
        )

        return {
            "repaired_ticks": repaired_ticks or 0,
            "repaired_events": repaired_events or 0,
            "repaired_snapshots": repaired_snapshots or 0,
            "repaired_profiles": repaired_profiles or 0,
            "deleted_invalid_daily_klines": deleted_invalid_daily_klines or 0,
            "repaired_mootdx_tick_volumes": repaired_mootdx_tick_volumes or 0,
            "repaired_mootdx_kline_volumes": repaired_mootdx_kline_volumes or 0,
            "repaired_tencent_tick_volumes": repaired_tencent_tick_volumes or 0,
            "repaired_tencent_kline_volumes": repaired_tencent_kline_volumes or 0,
            "repaired_mootdx_event_volumes": repaired_mootdx_event_volumes or 0,
            "repaired_tencent_event_volumes": repaired_tencent_event_volumes or 0,
        }

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def persist_market_state(
        self,
        *,
        snapshot: dict[str, object],
        tick: dict[str, object],
        kline: dict[str, object],
        event: dict[str, object],
    ) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        tick_ts = _coerce_utc_datetime(tick["ts"], field_name="tick.ts")
        kline_bucket_ts = _normalize_kline_bucket_ts(kline["bucketTs"], kline["period"])

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO stock_profile (symbol, name, exchange, updated_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = EXCLUDED.name,
                        exchange = EXCLUDED.exchange,
                        updated_at = EXCLUDED.updated_at
                    """,
                    snapshot["symbol"],
                    snapshot.get("companyName"),
                    snapshot.get("exchange"),
                    tick_ts,
                )

                await connection.execute(
                    """
                    INSERT INTO stock_snapshot (
                        symbol,
                        last_price,
                        change_pct,
                        pe,
                        pb,
                        turnover_rate,
                        market_cap,
                        limit_up,
                        limit_down,
                        updated_at,
                        payload
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb
                    )
                    ON CONFLICT (symbol) DO UPDATE SET
                        last_price = EXCLUDED.last_price,
                        change_pct = EXCLUDED.change_pct,
                        pe = EXCLUDED.pe,
                        pb = EXCLUDED.pb,
                        turnover_rate = EXCLUDED.turnover_rate,
                        market_cap = EXCLUDED.market_cap,
                        limit_up = EXCLUDED.limit_up,
                        limit_down = EXCLUDED.limit_down,
                        updated_at = EXCLUDED.updated_at,
                        payload = EXCLUDED.payload
                    """,
                    snapshot["symbol"],
                    snapshot["lastPrice"],
                    snapshot["changePct"],
                    snapshot["pe"],
                    snapshot["pb"],
                    snapshot["turnoverRate"],
                    snapshot["marketCap"],
                    snapshot["limitUp"],
                    snapshot["limitDown"],
                    tick_ts,
                    json.dumps(snapshot),
                )

                await connection.execute(
                    """
                    INSERT INTO stock_tick (ts, symbol, price, volume, amount, side, source, raw)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    """,
                    tick_ts,
                    tick["symbol"],
                    tick["price"],
                    tick["volume"],
                    tick["amount"],
                    tick["side"],
                    tick["source"],
                    json.dumps(tick["raw"]),
                )

                await connection.execute(
                    """
                    INSERT INTO stock_kline (
                        bucket_ts,
                        symbol,
                        period,
                        open,
                        high,
                        low,
                        close,
                        volume,
                        amount,
                        source,
                        raw
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb
                    )
                    ON CONFLICT (symbol, period, bucket_ts) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        amount = EXCLUDED.amount,
                        source = EXCLUDED.source,
                        raw = EXCLUDED.raw
                    """,
                    kline_bucket_ts,
                    kline["symbol"],
                    kline["period"],
                    kline["open"],
                    kline["high"],
                    kline["low"],
                    kline["close"],
                    kline["volume"],
                    kline["amount"],
                    kline["source"],
                    json.dumps(kline["raw"]),
                )

                await connection.execute(
                    """
                    INSERT INTO stock_event (ts, symbol, event_type, source, payload)
                    VALUES ($1, $2, $3, $4, $5::jsonb)
                    """,
                    tick_ts,
                    event["symbol"],
                    event["type"],
                    snapshot["source"],
                    json.dumps(event),
                )

    async def persist_kline_history(self, klines: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not klines:
            return

        rows = [
            (
                _normalize_kline_bucket_ts(item["bucketTs"], item["period"]),
                item["symbol"],
                item["period"],
                item["open"],
                item["high"],
                item["low"],
                item["close"],
                item.get("volume"),
                item.get("amount"),
                item["source"],
                json.dumps(item.get("raw", {})),
            )
            for item in klines
        ]

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.executemany(
                    """
                    INSERT INTO stock_kline (
                        bucket_ts,
                        symbol,
                        period,
                        open,
                        high,
                        low,
                        close,
                        volume,
                        amount,
                        source,
                        raw
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb
                    )
                    ON CONFLICT (symbol, period, bucket_ts) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        amount = EXCLUDED.amount,
                        source = EXCLUDED.source,
                        raw = EXCLUDED.raw
                    """,
                    rows,
                )

    async def persist_symbol_command(self, *, timestamp, symbol: str, command_type: str, payload: dict[str, object]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO symbol_command_log (ts, symbol, command_type, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                timestamp,
                symbol,
                command_type,
                json.dumps(payload),
            )

    async def ensure_content_checkpoint(self, *, lane: str, symbol: str, next_due_at: datetime) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO content_fetch_checkpoint (lane, symbol, cursor, next_due_at, failure_count)
                VALUES ($1, $2, '{}'::jsonb, $3, 0)
                ON CONFLICT (lane, symbol) DO NOTHING
                """,
                lane,
                symbol,
                _coerce_utc_datetime(next_due_at, field_name="checkpoint.next_due_at"),
            )

    async def upsert_content_checkpoint(
        self,
        *,
        lane: str,
        symbol: str,
        cursor: dict[str, object],
        next_due_at: datetime,
        cooldown_until: datetime | None,
        last_success_at: datetime | None,
        last_attempt_at: datetime | None,
        failure_count: int,
        last_error: str | None,
    ) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO content_fetch_checkpoint (
                    lane,
                    symbol,
                    cursor,
                    next_due_at,
                    cooldown_until,
                    last_success_at,
                    last_attempt_at,
                    failure_count,
                    last_error
                ) VALUES (
                    $1, $2, $3::jsonb, $4, $5, $6, $7, $8, $9
                )
                ON CONFLICT (lane, symbol) DO UPDATE SET
                    cursor = EXCLUDED.cursor,
                    next_due_at = EXCLUDED.next_due_at,
                    cooldown_until = EXCLUDED.cooldown_until,
                    last_success_at = EXCLUDED.last_success_at,
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    failure_count = EXCLUDED.failure_count,
                    last_error = EXCLUDED.last_error
                """,
                lane,
                symbol,
                _json_dumps(cursor),
                _coerce_utc_datetime(next_due_at, field_name="checkpoint.next_due_at"),
                _coerce_optional_utc_datetime(cooldown_until, field_name="checkpoint.cooldown_until"),
                _coerce_optional_utc_datetime(last_success_at, field_name="checkpoint.last_success_at"),
                _coerce_optional_utc_datetime(last_attempt_at, field_name="checkpoint.last_attempt_at"),
                failure_count,
                last_error,
            )

    async def fetch_content_checkpoints(self) -> list[dict[str, object]]:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT lane, symbol, cursor, next_due_at, cooldown_until, last_success_at, last_attempt_at, failure_count, last_error
                FROM content_fetch_checkpoint
                ORDER BY next_due_at ASC, lane ASC, symbol ASC
                """
            )

        items: list[dict[str, object]] = []
        for row in rows:
            items.append(
                {
                    "lane": row["lane"],
                    "symbol": row["symbol"],
                    "cursor": json.loads(row["cursor"]) if isinstance(row["cursor"], str) else dict(row["cursor"] or {}),
                    "next_due_at": row["next_due_at"],
                    "cooldown_until": row["cooldown_until"],
                    "last_success_at": row["last_success_at"],
                    "last_attempt_at": row["last_attempt_at"],
                    "failure_count": int(row["failure_count"] or 0),
                    "last_error": row["last_error"],
                }
            )
        return items

    async def delete_symbol_content_checkpoints(self, symbol: str) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        async with self._pool.acquire() as connection:
            await connection.execute("DELETE FROM content_fetch_checkpoint WHERE symbol = $1", symbol)

    async def delete_inactive_symbol_content_checkpoints(self, active_symbols: list[str]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        async with self._pool.acquire() as connection:
            if active_symbols:
                await connection.execute(
                    "DELETE FROM content_fetch_checkpoint WHERE symbol <> '' AND NOT (symbol = ANY($1::text[]))",
                    active_symbols,
                )
                return
            await connection.execute("DELETE FROM content_fetch_checkpoint WHERE symbol <> ''")

    async def insert_content_fetch_log(
        self,
        *,
        lane: str,
        symbol: str | None,
        provider: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        http_hint: str | None,
        error_message: str | None,
        meta: dict[str, object],
    ) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO content_fetch_log (lane, symbol, provider, status, started_at, finished_at, http_hint, error_message, meta)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                """,
                lane,
                symbol,
                provider,
                status,
                _coerce_utc_datetime(started_at, field_name="content_log.started_at"),
                _coerce_utc_datetime(finished_at, field_name="content_log.finished_at"),
                http_hint,
                error_message,
                _json_dumps(meta),
            )

    async def upsert_research_reports(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item["symbol"],
                item["title"],
                item.get("rating"),
                item.get("institution"),
                item.get("analyst"),
                item.get("industry"),
                _coerce_optional_utc_datetime(item.get("published_at"), field_name="report.published_at"),
                _coerce_utc_datetime(item["first_seen_at"], field_name="report.first_seen_at"),
                _coerce_utc_datetime(item["last_seen_at"], field_name="report.last_seen_at"),
                item.get("source_url"),
                item.get("provider", "akshare"),
                item["upstream_source"],
                item["dedupe_key"],
                _json_dumps(item.get("metrics", {})),
                _json_dumps(item.get("raw_payload", {})),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO stock_research_report (
                    symbol,
                    title,
                    rating,
                    institution,
                    analyst,
                    industry,
                    published_at,
                    first_seen_at,
                    last_seen_at,
                    source_url,
                    provider,
                    upstream_source,
                    dedupe_key,
                    metrics,
                    raw_payload
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb, $15::jsonb
                )
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    rating = EXCLUDED.rating,
                    institution = EXCLUDED.institution,
                    analyst = EXCLUDED.analyst,
                    industry = EXCLUDED.industry,
                    published_at = EXCLUDED.published_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    source_url = EXCLUDED.source_url,
                    provider = EXCLUDED.provider,
                    upstream_source = EXCLUDED.upstream_source,
                    metrics = EXCLUDED.metrics,
                    raw_payload = EXCLUDED.raw_payload
                """,
                rows,
            )

    async def upsert_news_items(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item.get("symbol"),
                item["scope"],
                item["title"],
                item.get("summary"),
                item.get("content"),
                item.get("article_source"),
                _coerce_optional_utc_datetime(item.get("published_at"), field_name="news.published_at"),
                _coerce_utc_datetime(item["first_seen_at"], field_name="news.first_seen_at"),
                _coerce_utc_datetime(item["last_seen_at"], field_name="news.last_seen_at"),
                item.get("source_url"),
                item.get("provider", "akshare"),
                item["upstream_source"],
                item["dedupe_key"],
                _json_dumps(item.get("raw_payload", {})),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO stock_news_item (
                    symbol,
                    scope,
                    title,
                    summary,
                    content,
                    article_source,
                    published_at,
                    first_seen_at,
                    last_seen_at,
                    source_url,
                    provider,
                    upstream_source,
                    dedupe_key,
                    raw_payload
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb
                )
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    content = EXCLUDED.content,
                    article_source = EXCLUDED.article_source,
                    published_at = EXCLUDED.published_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    source_url = EXCLUDED.source_url,
                    provider = EXCLUDED.provider,
                    upstream_source = EXCLUDED.upstream_source,
                    raw_payload = EXCLUDED.raw_payload
                """,
                rows,
            )

    async def upsert_announcement_items(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item["symbol"],
                item["title"],
                item.get("announcement_type"),
                _coerce_optional_utc_datetime(item.get("published_at"), field_name="announcement.published_at"),
                _coerce_utc_datetime(item["first_seen_at"], field_name="announcement.first_seen_at"),
                _coerce_utc_datetime(item["last_seen_at"], field_name="announcement.last_seen_at"),
                item.get("pdf_url"),
                item.get("provider", "akshare"),
                item["upstream_source"],
                item["dedupe_key"],
                _json_dumps(item.get("raw_payload", {})),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO stock_announcement_item (
                    symbol,
                    title,
                    announcement_type,
                    published_at,
                    first_seen_at,
                    last_seen_at,
                    pdf_url,
                    provider,
                    upstream_source,
                    dedupe_key,
                    raw_payload
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb
                )
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    announcement_type = EXCLUDED.announcement_type,
                    published_at = EXCLUDED.published_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    pdf_url = EXCLUDED.pdf_url,
                    provider = EXCLUDED.provider,
                    upstream_source = EXCLUDED.upstream_source,
                    raw_payload = EXCLUDED.raw_payload
                """,
                rows,
            )
