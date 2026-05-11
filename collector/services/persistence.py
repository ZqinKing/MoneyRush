from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import asyncpg


logger = logging.getLogger(__name__)


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

        return {
            "repaired_ticks": repaired_ticks or 0,
            "repaired_events": repaired_events or 0,
            "repaired_snapshots": repaired_snapshots or 0,
            "repaired_profiles": repaired_profiles or 0,
            "deleted_invalid_daily_klines": deleted_invalid_daily_klines or 0,
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
