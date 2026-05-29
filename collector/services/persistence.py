from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg


logger = logging.getLogger(__name__)


def _json_dumps(value: object) -> str:
    return json.dumps(_json_safe(value), default=str, allow_nan=False)


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


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


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int, Decimal)):
        return float(value)
    return None


def _to_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, float):
        return int(value)
    return None


def _decode_jsonish(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


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
            exact_duplicate_cleanup = await self._dedupe_exact_market_rows(connection)
            if any(exact_duplicate_cleanup.values()):
                logger.warning("collector removed exact duplicate market rows", extra=exact_duplicate_cleanup)

            await connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS stock_tick_identity_idx ON stock_tick (symbol, ts, source, price, COALESCE(volume, -1), COALESCE(amount, -1), COALESCE(side, ''))"
            )
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
                "CREATE UNIQUE INDEX IF NOT EXISTS stock_event_identity_idx ON stock_event (symbol, ts, event_type, source, payload)"
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
                    ai_summary TEXT,
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
                "ALTER TABLE stock_news_item ADD COLUMN IF NOT EXISTS ai_summary TEXT"
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
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dragon_tiger_daily_item (
                    trade_date DATE NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    close_price NUMERIC(18, 4),
                    change_percent NUMERIC(10, 4),
                    net_buy_amount NUMERIC(20, 2),
                    buy_amount NUMERIC(20, 2),
                    sell_amount NUMERIC(20, 2),
                    deal_amount NUMERIC(20, 2),
                    total_amount NUMERIC(20, 2),
                    net_buy_ratio NUMERIC(18, 4),
                    deal_amount_ratio NUMERIC(18, 4),
                    turnover_rate NUMERIC(18, 4),
                    free_market_cap NUMERIC(20, 2),
                    explain TEXT,
                    reason TEXT,
                    after_1d NUMERIC(10, 4),
                    after_2d NUMERIC(10, 4),
                    after_5d NUMERIC(10, 4),
                    after_10d NUMERIC(10, 4),
                    source TEXT NOT NULL,
                    generated_at TIMESTAMPTZ,
                    collected_at TIMESTAMPTZ NOT NULL,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    PRIMARY KEY (trade_date, symbol)
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS dragon_tiger_daily_item_symbol_trade_idx ON dragon_tiger_daily_item (symbol, trade_date DESC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dragon_tiger_institution_item (
                    trade_date DATE NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    close_price NUMERIC(18, 4),
                    change_percent NUMERIC(10, 4),
                    buy_org_count INTEGER,
                    sell_org_count INTEGER,
                    org_buy_amount NUMERIC(20, 2),
                    org_sell_amount NUMERIC(20, 2),
                    org_net_amount NUMERIC(20, 2),
                    market_total_amount NUMERIC(20, 2),
                    org_net_amount_ratio NUMERIC(18, 4),
                    turnover_rate NUMERIC(18, 4),
                    free_market_cap NUMERIC(20, 2),
                    reason TEXT,
                    source TEXT NOT NULL,
                    generated_at TIMESTAMPTZ,
                    collected_at TIMESTAMPTZ NOT NULL,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    PRIMARY KEY (trade_date, symbol)
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS dragon_tiger_institution_item_symbol_trade_idx ON dragon_tiger_institution_item (symbol, trade_date DESC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dragon_tiger_collection_checkpoint (
                    job_name TEXT PRIMARY KEY,
                    next_due_at TIMESTAMPTZ NOT NULL,
                    cooldown_until TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_attempt_at TIMESTAMPTZ,
                    last_collected_trade_date DATE,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS dragon_tiger_collection_checkpoint_next_due_idx ON dragon_tiger_collection_checkpoint (next_due_at ASC)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dragon_tiger_collection_log (
                    id BIGSERIAL PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ NOT NULL,
                    trade_date DATE,
                    error_message TEXT,
                    meta JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS dragon_tiger_collection_log_job_started_idx ON dragon_tiger_collection_log (job_name, started_at DESC)"
            )
            await self._ensure_fund_schema(connection)
            await self._ensure_anomaly_schema(connection)
            if self._enable_runtime_data_repair:
                repairs = await self._repair_runtime_data(connection)
                if any(repairs.values()):
                    logger.warning("collector repaired persisted market data", extra=repairs)

    async def _ensure_fund_schema(self, connection: asyncpg.Connection) -> None:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_profile (
                fund_code TEXT PRIMARY KEY,
                fund_name TEXT NOT NULL,
                fund_type TEXT,
                fund_company TEXT,
                manager_name TEXT,
                established_date DATE,
                risk_level TEXT,
                benchmark_index TEXT,
                management_fee NUMERIC(8, 4),
                custody_fee NUMERIC(8, 4),
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_nav (
                fund_code TEXT NOT NULL,
                nav_date DATE NOT NULL,
                nav NUMERIC(18, 6),
                accum_nav NUMERIC(18, 6),
                daily_return NUMERIC(10, 4),
                source TEXT NOT NULL,
                raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (fund_code, nav_date)
            )
            """
        )
        await connection.execute("CREATE INDEX IF NOT EXISTS fund_nav_code_date_idx ON fund_nav (fund_code, nav_date DESC)")
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_snapshot (
                fund_code TEXT PRIMARY KEY,
                nav NUMERIC(18, 6),
                daily_return NUMERIC(10, 4),
                nav_date DATE,
                estimated_intraday_return NUMERIC(10, 4),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                payload JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_stock_holding (
                fund_code TEXT NOT NULL,
                stock_symbol TEXT NOT NULL,
                stock_market TEXT,
                stock_name TEXT,
                report_date DATE NOT NULL,
                rank INTEGER,
                weight_percent NUMERIC(10, 4),
                hold_shares BIGINT,
                hold_market_value NUMERIC(20, 2),
                change_type TEXT,
                raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (fund_code, stock_symbol, report_date)
            )
            """
        )
        await connection.execute("ALTER TABLE fund_stock_holding ADD COLUMN IF NOT EXISTS stock_market TEXT")
        await connection.execute("CREATE INDEX IF NOT EXISTS fund_stock_holding_fund_report_idx ON fund_stock_holding (fund_code, report_date DESC, rank ASC)")
        await connection.execute("CREATE INDEX IF NOT EXISTS fund_stock_holding_stock_report_idx ON fund_stock_holding (stock_symbol, report_date DESC)")
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_fund_holding (
                stock_symbol TEXT NOT NULL,
                fund_code TEXT NOT NULL,
                fund_name TEXT,
                fund_type TEXT,
                report_date DATE NOT NULL,
                weight_percent NUMERIC(10, 4),
                hold_market_value NUMERIC(20, 2),
                change_type TEXT,
                raw JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (stock_symbol, fund_code, report_date)
            )
            """
        )
        await connection.execute("CREATE INDEX IF NOT EXISTS stock_fund_holding_stock_report_idx ON stock_fund_holding (stock_symbol, report_date DESC)")
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_stock_link (
                fund_code TEXT NOT NULL,
                stock_symbol TEXT NOT NULL,
                link_type TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (fund_code, stock_symbol)
            )
            """
        )
        await connection.execute("CREATE INDEX IF NOT EXISTS fund_stock_link_symbol_idx ON fund_stock_link (stock_symbol)")
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_command_log (
                ts TIMESTAMPTZ NOT NULL,
                fund_code TEXT NOT NULL,
                command_type TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        await connection.execute("SELECT create_hypertable('fund_command_log', 'ts', if_not_exists => TRUE, migrate_data => TRUE)")
        await connection.execute("CREATE INDEX IF NOT EXISTS fund_command_log_fund_ts_idx ON fund_command_log (fund_code, ts DESC)")

    async def _ensure_anomaly_schema(self, connection: asyncpg.Connection) -> None:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS significant_anomaly (
                id BIGSERIAL PRIMARY KEY,
                anomaly_date DATE NOT NULL,
                symbol TEXT NOT NULL,
                anomaly_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                trigger_price NUMERIC(18, 4),
                reference_price NUMERIC(18, 4),
                change_pct NUMERIC(10, 4),
                trigger_volume BIGINT,
                volume_ratio NUMERIC(10, 4),
                first_trigger_ts TIMESTAMPTZ NOT NULL,
                last_trigger_ts TIMESTAMPTZ,
                duration_minutes INTEGER,
                event_count INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'collector-anomaly-aggregator',
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                ai_reason TEXT,
                ai_reason_status TEXT NOT NULL DEFAULT 'pending',
                ai_reason_generated_at TIMESTAMPTZ,
                related_news_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                related_announcement_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                first_trigger_bucket TIMESTAMPTZ NOT NULL,
                UNIQUE (anomaly_date, symbol, anomaly_type, first_trigger_bucket)
            )
            """
        )
        await connection.execute("ALTER TABLE significant_anomaly ADD COLUMN IF NOT EXISTS ai_reason TEXT")
        await connection.execute("ALTER TABLE significant_anomaly ADD COLUMN IF NOT EXISTS ai_reason_status TEXT NOT NULL DEFAULT 'pending'")
        await connection.execute("ALTER TABLE significant_anomaly ADD COLUMN IF NOT EXISTS ai_reason_generated_at TIMESTAMPTZ")
        await connection.execute("ALTER TABLE significant_anomaly ADD COLUMN IF NOT EXISTS related_news_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
        await connection.execute("ALTER TABLE significant_anomaly ADD COLUMN IF NOT EXISTS related_announcement_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
        await connection.execute("ALTER TABLE significant_anomaly ADD COLUMN IF NOT EXISTS first_trigger_bucket TIMESTAMPTZ")
        await connection.execute("UPDATE significant_anomaly SET first_trigger_bucket = date_trunc('hour', first_trigger_ts) WHERE first_trigger_bucket IS NULL")
        await connection.execute("ALTER TABLE significant_anomaly ALTER COLUMN first_trigger_bucket SET NOT NULL")
        await connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS significant_anomaly_identity_idx ON significant_anomaly (anomaly_date, symbol, anomaly_type, first_trigger_bucket)"
        )
        await connection.execute("CREATE INDEX IF NOT EXISTS significant_anomaly_date_idx ON significant_anomaly (anomaly_date DESC, severity)")
        await connection.execute("CREATE INDEX IF NOT EXISTS significant_anomaly_symbol_date_idx ON significant_anomaly (symbol, anomaly_date DESC)")
        await connection.execute("CREATE INDEX IF NOT EXISTS significant_anomaly_first_trigger_idx ON significant_anomaly (first_trigger_ts DESC)")
        await connection.execute("CREATE INDEX IF NOT EXISTS significant_anomaly_ai_status_idx ON significant_anomaly (ai_reason_status, anomaly_date DESC)")

    async def _dedupe_exact_market_rows(self, connection: asyncpg.Connection) -> dict[str, int]:
        removed_tick_duplicates = await connection.fetchval(
            """
            WITH ranked AS (
                SELECT ctid,
                       ROW_NUMBER() OVER (
                           PARTITION BY ts, symbol, price, volume, amount, side, source
                           ORDER BY ctid
                       ) AS duplicate_rank
                FROM stock_tick
            ), deleted AS (
                DELETE FROM stock_tick
                WHERE ctid IN (
                    SELECT ctid
                    FROM ranked
                    WHERE duplicate_rank > 1
                )
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM deleted
            """
        )
        removed_event_duplicates = await connection.fetchval(
            """
            WITH ranked AS (
                SELECT ctid,
                       ROW_NUMBER() OVER (
                           PARTITION BY ts, symbol, event_type, source, payload
                           ORDER BY ctid
                       ) AS duplicate_rank
                FROM stock_event
            ), deleted AS (
                DELETE FROM stock_event
                WHERE ctid IN (
                    SELECT ctid
                    FROM ranked
                    WHERE duplicate_rank > 1
                )
                RETURNING 1
            )
            SELECT COUNT(*)::int FROM deleted
            """
        )

        return {
            "removed_tick_duplicates": removed_tick_duplicates or 0,
            "removed_event_duplicates": removed_event_duplicates or 0,
        }

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

    async def fetch_records(self, query: str, *args: object) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, *args)

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
        event_payload_json = json.dumps(event)

        async with self._pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    SELECT pg_advisory_xact_lock(hashtextextended($1, 0))
                    """,
                    tick["symbol"],
                )

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

                latest_tick_row = await connection.fetchrow(
                    """
                    SELECT price, volume, amount, side, source
                    FROM stock_tick
                    WHERE symbol = $1
                    ORDER BY ts DESC
                    LIMIT 1
                    """,
                    tick["symbol"],
                )
                latest_event_row = await connection.fetchrow(
                    """
                    SELECT event_type, source, payload
                    FROM stock_event
                    WHERE symbol = $1
                    ORDER BY ts DESC
                    LIMIT 1
                    """,
                    event["symbol"],
                )

                latest_tick_matches = False
                if latest_tick_row is not None:
                    latest_tick_matches = (
                        _to_float(latest_tick_row["price"]) == _to_float(tick["price"])
                        and _to_int(latest_tick_row["volume"]) == _to_int(tick["volume"])
                        and _to_float(latest_tick_row["amount"]) == _to_float(tick["amount"])
                        and latest_tick_row["side"] == tick["side"]
                        and latest_tick_row["source"] == tick["source"]
                    )

                if not latest_tick_matches:
                    await connection.execute(
                        """
                        INSERT INTO stock_tick (ts, symbol, price, volume, amount, side, source, raw)
                        SELECT $1, $2, $3, $4, $5, $6, $7, $8::jsonb
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM stock_tick
                            WHERE ts = $1
                              AND symbol = $2
                              AND price IS NOT DISTINCT FROM $3
                              AND volume IS NOT DISTINCT FROM $4
                              AND amount IS NOT DISTINCT FROM $5
                              AND side IS NOT DISTINCT FROM $6
                              AND source = $7
                        )
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

                latest_event_matches = False
                if latest_event_row is not None:
                    latest_event_payload = _decode_jsonish(latest_event_row["payload"]) or {}
                    latest_event_tick = latest_event_payload.get("tick") if isinstance(latest_event_payload.get("tick"), dict) else {}
                    current_event_tick = event.get("tick") if isinstance(event.get("tick"), dict) else {}
                    latest_event_kline = latest_event_payload.get("kline") if isinstance(latest_event_payload.get("kline"), dict) else {}
                    current_event_kline = event.get("kline") if isinstance(event.get("kline"), dict) else {}

                    latest_event_matches = (
                        latest_event_row["event_type"] == event["type"]
                        and latest_event_row["source"] == snapshot["source"]
                        and _to_float(latest_event_tick.get("price")) == _to_float(current_event_tick.get("price"))
                        and _to_int(latest_event_tick.get("volume")) == _to_int(current_event_tick.get("volume"))
                        and latest_event_tick.get("side") == current_event_tick.get("side")
                        and latest_event_kline.get("period") == current_event_kline.get("period")
                        and _to_float(latest_event_kline.get("close")) == _to_float(current_event_kline.get("close"))
                        and _to_float(latest_event_kline.get("high")) == _to_float(current_event_kline.get("high"))
                        and _to_float(latest_event_kline.get("low")) == _to_float(current_event_kline.get("low"))
                    )

                if not latest_event_matches:
                    await connection.execute(
                        """
                        INSERT INTO stock_event (ts, symbol, event_type, source, payload)
                        SELECT $1, $2, $3, $4, $5::jsonb
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM stock_event
                            WHERE ts = $1
                              AND symbol = $2
                              AND event_type = $3
                              AND source = $4
                              AND payload = $5::jsonb
                        )
                        """,
                        tick_ts,
                        event["symbol"],
                        event["type"],
                        snapshot["source"],
                        event_payload_json,
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
                _json_dumps(payload),
            )

    async def persist_fund_command(self, *, timestamp, fund_code: str, command_type: str, payload: dict[str, object]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO fund_command_log (ts, fund_code, command_type, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                timestamp,
                fund_code,
                command_type,
                _json_dumps(payload),
            )

    async def upsert_significant_anomalies(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item["anomaly_date"],
                item["symbol"],
                item["anomaly_type"],
                item["severity"],
                _to_float(item.get("trigger_price")),
                _to_float(item.get("reference_price")),
                _to_float(item.get("change_pct")),
                _to_int(item.get("trigger_volume")),
                _to_float(item.get("volume_ratio")),
                _coerce_utc_datetime(item["first_trigger_ts"], field_name="significant_anomaly.first_trigger_ts"),
                _coerce_optional_utc_datetime(item.get("last_trigger_ts"), field_name="significant_anomaly.last_trigger_ts"),
                _to_int(item.get("duration_minutes")),
                _to_int(item.get("event_count")) or 1,
                item.get("source", "collector-anomaly-aggregator"),
                _json_dumps(item.get("payload", {})),
                _coerce_utc_datetime(item["first_trigger_bucket"], field_name="significant_anomaly.first_trigger_bucket"),
                item.get("ai_reason_status", "pending"),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO significant_anomaly (
                    anomaly_date, symbol, anomaly_type, severity, trigger_price, reference_price,
                    change_pct, trigger_volume, volume_ratio, first_trigger_ts, last_trigger_ts,
                    duration_minutes, event_count, source, payload, first_trigger_bucket, ai_reason_status, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9, $10, $11,
                    $12, $13, $14, $15::jsonb, $16, $17, NOW()
                )
                ON CONFLICT (anomaly_date, symbol, anomaly_type, first_trigger_bucket) DO UPDATE SET
                    severity = EXCLUDED.severity,
                    trigger_price = EXCLUDED.trigger_price,
                    reference_price = EXCLUDED.reference_price,
                    change_pct = EXCLUDED.change_pct,
                    trigger_volume = EXCLUDED.trigger_volume,
                    volume_ratio = EXCLUDED.volume_ratio,
                    last_trigger_ts = EXCLUDED.last_trigger_ts,
                    duration_minutes = EXCLUDED.duration_minutes,
                    event_count = EXCLUDED.event_count,
                    source = EXCLUDED.source,
                    payload = EXCLUDED.payload,
                    ai_reason_status = CASE
                        WHEN significant_anomaly.ai_reason IS NULL
                        THEN EXCLUDED.ai_reason_status
                        ELSE significant_anomaly.ai_reason_status
                    END,
                    updated_at = NOW()
                """,
                rows,
            )

    async def fetch_pending_anomaly_reasons(self, *, limit: int = 10) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            return await connection.fetch(
                """
                SELECT id, anomaly_date, symbol, anomaly_type, severity, trigger_price, reference_price,
                       change_pct, trigger_volume, volume_ratio, first_trigger_ts, last_trigger_ts,
                       duration_minutes, event_count, payload
                FROM significant_anomaly
                WHERE severity = ANY($1::text[])
                  AND (ai_reason_status IS NULL OR ai_reason_status IN ('pending', 'failed', 'skipped'))
                  AND ai_reason IS NULL
                ORDER BY anomaly_date DESC, first_trigger_ts DESC
                LIMIT $2
                """,
                ["critical", "high"],
                max(int(limit), 1),
            )

    async def fetch_anomaly_reason_context(self, *, symbol: str, since_ts: datetime, until_ts: datetime, limit_per_kind: int = 5) -> dict[str, list[asyncpg.Record]]:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        limit_value = max(int(limit_per_kind), 1)

        async with self._pool.acquire() as connection:
            news_rows = await connection.fetch(
                """
                SELECT id, dedupe_key, title, summary, content, article_source, published_at, first_seen_at, ai_summary
                FROM stock_news_item
                WHERE (symbol = $1 OR symbol IS NULL)
                  AND first_seen_at >= $2
                  AND first_seen_at <= $3
                ORDER BY first_seen_at DESC
                LIMIT $4
                """,
                symbol,
                since_ts,
                until_ts,
                limit_value,
            )
            announcement_rows = await connection.fetch(
                """
                SELECT id, dedupe_key, title, announcement_type, published_at, first_seen_at
                FROM stock_announcement_item
                WHERE symbol = $1
                  AND first_seen_at >= $2
                  AND first_seen_at <= $3
                ORDER BY first_seen_at DESC
                LIMIT $4
                """,
                symbol,
                since_ts,
                until_ts,
                limit_value,
            )
            report_rows = await connection.fetch(
                """
                SELECT id, dedupe_key, title, rating, institution, analyst, industry, published_at, first_seen_at
                FROM stock_research_report
                WHERE symbol = $1
                  AND first_seen_at >= $2
                  AND first_seen_at <= $3
                ORDER BY first_seen_at DESC
                LIMIT $4
                """,
                symbol,
                since_ts,
                until_ts,
                limit_value,
            )

        return {
            "news": news_rows,
            "announcements": announcement_rows,
            "reports": report_rows,
        }

    async def update_anomaly_ai_reasons(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item["id"],
                item.get("ai_reason"),
                item["ai_reason_status"],
                _coerce_optional_utc_datetime(item.get("ai_reason_generated_at"), field_name="significant_anomaly.ai_reason_generated_at"),
                _json_dumps(item.get("related_news_ids", [])),
                _json_dumps(item.get("related_announcement_ids", [])),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                UPDATE significant_anomaly
                SET ai_reason = $2,
                    ai_reason_status = $3,
                    ai_reason_generated_at = $4,
                    related_news_ids = $5::jsonb,
                    related_announcement_ids = $6::jsonb,
                    updated_at = NOW()
                WHERE id = $1
                """,
                rows,
            )

    async def upsert_fund_profile(self, payload: dict[str, object]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO fund_profile (
                    fund_code, fund_name, fund_type, fund_company, manager_name, established_date, risk_level,
                    benchmark_index, management_fee, custody_fee, payload, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, now()
                )
                ON CONFLICT (fund_code) DO UPDATE SET
                    fund_name = EXCLUDED.fund_name,
                    fund_type = EXCLUDED.fund_type,
                    fund_company = EXCLUDED.fund_company,
                    manager_name = EXCLUDED.manager_name,
                    established_date = EXCLUDED.established_date,
                    risk_level = EXCLUDED.risk_level,
                    benchmark_index = EXCLUDED.benchmark_index,
                    management_fee = EXCLUDED.management_fee,
                    custody_fee = EXCLUDED.custody_fee,
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                payload["fundCode"],
                payload.get("fundName"),
                payload.get("fundType"),
                payload.get("fundCompany"),
                payload.get("managerName"),
                payload.get("establishedDate"),
                payload.get("riskLevel"),
                payload.get("benchmarkIndex"),
                payload.get("managementFee"),
                payload.get("custodyFee"),
                _json_dumps(payload),
            )

    async def upsert_fund_snapshot(self, payload: dict[str, object]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO fund_snapshot (
                    fund_code, nav, daily_return, nav_date, estimated_intraday_return, updated_at, payload
                ) VALUES ($1, $2, $3, $4, $5, now(), $6::jsonb)
                ON CONFLICT (fund_code) DO UPDATE SET
                    nav = EXCLUDED.nav,
                    daily_return = EXCLUDED.daily_return,
                    nav_date = EXCLUDED.nav_date,
                    estimated_intraday_return = EXCLUDED.estimated_intraday_return,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """,
                payload["fundCode"],
                payload.get("nav"),
                payload.get("dailyReturn"),
                payload.get("navDate"),
                payload.get("estimatedIntradayReturn"),
                _json_dumps(payload),
            )

    async def upsert_fund_nav_rows(self, rows: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not rows:
            return

        values = [
            (row["fund_code"], row["nav_date"], row.get("nav"), row.get("accum_nav"), row.get("daily_return"), row.get("source", "akshare"), _json_dumps(row.get("raw", {})))
            for row in rows
        ]
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO fund_nav (fund_code, nav_date, nav, accum_nav, daily_return, source, raw)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                ON CONFLICT (fund_code, nav_date) DO UPDATE SET
                    nav = EXCLUDED.nav,
                    accum_nav = EXCLUDED.accum_nav,
                    daily_return = EXCLUDED.daily_return,
                    source = EXCLUDED.source,
                    raw = EXCLUDED.raw
                """,
                values,
            )

    async def upsert_fund_holding_rows(self, rows: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not rows:
            return

        values = [
            (
                row["fund_code"],
                row["stock_symbol"],
                row.get("stock_market"),
                row.get("stock_name"),
                row["report_date"],
                row.get("rank"),
                row.get("weight_percent"),
                row.get("hold_shares"),
                row.get("hold_market_value"),
                row.get("change_type"),
                _json_dumps(row.get("raw", {})),
            )
            for row in rows
        ]
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO fund_stock_holding (
                    fund_code, stock_symbol, stock_market, stock_name, report_date, rank, weight_percent, hold_shares,
                    hold_market_value, change_type, raw
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT (fund_code, stock_symbol, report_date) DO UPDATE SET
                    stock_market = EXCLUDED.stock_market,
                    stock_name = EXCLUDED.stock_name,
                    rank = EXCLUDED.rank,
                    weight_percent = EXCLUDED.weight_percent,
                    hold_shares = EXCLUDED.hold_shares,
                    hold_market_value = EXCLUDED.hold_market_value,
                    change_type = EXCLUDED.change_type,
                    raw = EXCLUDED.raw
                """,
                values,
            )

    async def upsert_stock_fund_holding_rows(self, rows: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not rows:
            return

        values = [
            (
                row["stock_symbol"],
                row["fund_code"],
                row.get("fund_name"),
                row.get("fund_type"),
                row["report_date"],
                row.get("weight_percent"),
                row.get("hold_market_value"),
                row.get("change_type"),
                _json_dumps(row.get("raw", {})),
            )
            for row in rows
        ]
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO stock_fund_holding (
                    stock_symbol, fund_code, fund_name, fund_type, report_date, weight_percent,
                    hold_market_value, change_type, raw
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                ON CONFLICT (stock_symbol, fund_code, report_date) DO UPDATE SET
                    fund_name = EXCLUDED.fund_name,
                    fund_type = EXCLUDED.fund_type,
                    weight_percent = EXCLUDED.weight_percent,
                    hold_market_value = EXCLUDED.hold_market_value,
                    change_type = EXCLUDED.change_type,
                    raw = EXCLUDED.raw
                """,
                values,
            )

    async def upsert_fund_stock_links(self, rows: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not rows:
            return

        values = [(row["fund_code"], row["stock_symbol"], row.get("link_type", "top-holding")) for row in rows]
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO fund_stock_link (fund_code, stock_symbol, link_type)
                VALUES ($1, $2, $3)
                ON CONFLICT (fund_code, stock_symbol) DO UPDATE SET
                    link_type = EXCLUDED.link_type
                """,
                values,
            )

    async def delete_fund_stock_links(self, fund_code: str, exclude_stock_symbols: list[str] | None = None) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        async with self._pool.acquire() as connection:
            if exclude_stock_symbols:
                await connection.execute(
                    """
                    DELETE FROM fund_stock_link
                    WHERE fund_code = $1
                      AND NOT (stock_symbol = ANY($2::text[]))
                    """,
                    fund_code,
                    exclude_stock_symbols,
                )
            else:
                await connection.execute("DELETE FROM fund_stock_link WHERE fund_code = $1", fund_code)

    async def has_other_fund_stock_links(self, *, stock_symbol: str, excluding_fund_code: str) -> bool:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        async with self._pool.acquire() as connection:
            return bool(
                await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM fund_stock_link
                        WHERE stock_symbol = $1
                          AND fund_code <> $2
                    )
                    """,
                    stock_symbol,
                    excluding_fund_code,
                )
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

    async def ensure_dragon_tiger_checkpoint(self, *, job_name: str, next_due_at: datetime) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO dragon_tiger_collection_checkpoint (job_name, next_due_at, failure_count)
                VALUES ($1, $2, 0)
                ON CONFLICT (job_name) DO NOTHING
                """,
                job_name,
                _coerce_utc_datetime(next_due_at, field_name="dragon_tiger_checkpoint.next_due_at"),
            )

    async def fetch_dragon_tiger_checkpoints(self) -> list[dict[str, object]]:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT job_name, next_due_at, cooldown_until, last_success_at, last_attempt_at, last_collected_trade_date, failure_count, last_error
                FROM dragon_tiger_collection_checkpoint
                ORDER BY next_due_at ASC, job_name ASC
                """
            )

        return [
            {
                "job_name": row["job_name"],
                "next_due_at": row["next_due_at"],
                "cooldown_until": row["cooldown_until"],
                "last_success_at": row["last_success_at"],
                "last_attempt_at": row["last_attempt_at"],
                "last_collected_trade_date": row["last_collected_trade_date"],
                "failure_count": int(row["failure_count"] or 0),
                "last_error": row["last_error"],
            }
            for row in rows
        ]

    async def upsert_dragon_tiger_checkpoint(
        self,
        *,
        job_name: str,
        next_due_at: datetime,
        cooldown_until: datetime | None,
        last_success_at: datetime | None,
        last_attempt_at: datetime | None,
        last_collected_trade_date,
        failure_count: int,
        last_error: str | None,
    ) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO dragon_tiger_collection_checkpoint (
                    job_name,
                    next_due_at,
                    cooldown_until,
                    last_success_at,
                    last_attempt_at,
                    last_collected_trade_date,
                    failure_count,
                    last_error
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (job_name) DO UPDATE SET
                    next_due_at = EXCLUDED.next_due_at,
                    cooldown_until = EXCLUDED.cooldown_until,
                    last_success_at = EXCLUDED.last_success_at,
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    last_collected_trade_date = EXCLUDED.last_collected_trade_date,
                    failure_count = EXCLUDED.failure_count,
                    last_error = EXCLUDED.last_error
                """,
                job_name,
                _coerce_utc_datetime(next_due_at, field_name="dragon_tiger_checkpoint.next_due_at"),
                _coerce_optional_utc_datetime(cooldown_until, field_name="dragon_tiger_checkpoint.cooldown_until"),
                _coerce_optional_utc_datetime(last_success_at, field_name="dragon_tiger_checkpoint.last_success_at"),
                _coerce_optional_utc_datetime(last_attempt_at, field_name="dragon_tiger_checkpoint.last_attempt_at"),
                last_collected_trade_date,
                failure_count,
                last_error,
            )

    async def insert_dragon_tiger_collection_log(
        self,
        *,
        job_name: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        trade_date,
        error_message: str | None,
        meta: dict[str, object],
    ) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO dragon_tiger_collection_log (job_name, status, started_at, finished_at, trade_date, error_message, meta)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                """,
                job_name,
                status,
                _coerce_utc_datetime(started_at, field_name="dragon_tiger_log.started_at"),
                _coerce_utc_datetime(finished_at, field_name="dragon_tiger_log.finished_at"),
                trade_date,
                error_message,
                _json_dumps(meta),
            )

    async def upsert_dragon_tiger_daily_items(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item["trade_date"],
                item["symbol"],
                item.get("name"),
                item.get("close_price"),
                item.get("change_percent"),
                item.get("net_buy_amount"),
                item.get("buy_amount"),
                item.get("sell_amount"),
                item.get("deal_amount"),
                item.get("total_amount"),
                item.get("net_buy_ratio"),
                item.get("deal_amount_ratio"),
                item.get("turnover_rate"),
                item.get("free_market_cap"),
                item.get("explain"),
                item.get("reason"),
                item.get("after_1d"),
                item.get("after_2d"),
                item.get("after_5d"),
                item.get("after_10d"),
                item["source"],
                _coerce_optional_utc_datetime(item.get("generated_at"), field_name="dragon_tiger_daily.generated_at"),
                _coerce_utc_datetime(item["collected_at"], field_name="dragon_tiger_daily.collected_at"),
                _json_dumps(item.get("raw_payload", {})),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO dragon_tiger_daily_item (
                    trade_date, symbol, name, close_price, change_percent, net_buy_amount, buy_amount, sell_amount,
                    deal_amount, total_amount, net_buy_ratio, deal_amount_ratio, turnover_rate, free_market_cap,
                    explain, reason, after_1d, after_2d, after_5d, after_10d, source, generated_at, collected_at, raw_payload
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, $23, $24::jsonb
                )
                ON CONFLICT (trade_date, symbol) DO UPDATE SET
                    name = EXCLUDED.name,
                    close_price = EXCLUDED.close_price,
                    change_percent = EXCLUDED.change_percent,
                    net_buy_amount = EXCLUDED.net_buy_amount,
                    buy_amount = EXCLUDED.buy_amount,
                    sell_amount = EXCLUDED.sell_amount,
                    deal_amount = EXCLUDED.deal_amount,
                    total_amount = EXCLUDED.total_amount,
                    net_buy_ratio = EXCLUDED.net_buy_ratio,
                    deal_amount_ratio = EXCLUDED.deal_amount_ratio,
                    turnover_rate = EXCLUDED.turnover_rate,
                    free_market_cap = EXCLUDED.free_market_cap,
                    explain = EXCLUDED.explain,
                    reason = EXCLUDED.reason,
                    after_1d = EXCLUDED.after_1d,
                    after_2d = EXCLUDED.after_2d,
                    after_5d = EXCLUDED.after_5d,
                    after_10d = EXCLUDED.after_10d,
                    source = EXCLUDED.source,
                    generated_at = EXCLUDED.generated_at,
                    collected_at = EXCLUDED.collected_at,
                    raw_payload = EXCLUDED.raw_payload
                """,
                rows,
            )

    async def upsert_dragon_tiger_institution_items(self, items: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item["trade_date"],
                item["symbol"],
                item.get("name"),
                item.get("close_price"),
                item.get("change_percent"),
                item.get("buy_org_count"),
                item.get("sell_org_count"),
                item.get("org_buy_amount"),
                item.get("org_sell_amount"),
                item.get("org_net_amount"),
                item.get("market_total_amount"),
                item.get("org_net_amount_ratio"),
                item.get("turnover_rate"),
                item.get("free_market_cap"),
                item.get("reason"),
                item["source"],
                _coerce_optional_utc_datetime(item.get("generated_at"), field_name="dragon_tiger_institution.generated_at"),
                _coerce_utc_datetime(item["collected_at"], field_name="dragon_tiger_institution.collected_at"),
                _json_dumps(item.get("raw_payload", {})),
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO dragon_tiger_institution_item (
                    trade_date, symbol, name, close_price, change_percent, buy_org_count, sell_org_count,
                    org_buy_amount, org_sell_amount, org_net_amount, market_total_amount, org_net_amount_ratio,
                    turnover_rate, free_market_cap, reason, source, generated_at, collected_at, raw_payload
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7,
                    $8, $9, $10, $11, $12,
                    $13, $14, $15, $16, $17, $18, $19::jsonb
                )
                ON CONFLICT (trade_date, symbol) DO UPDATE SET
                    name = EXCLUDED.name,
                    close_price = EXCLUDED.close_price,
                    change_percent = EXCLUDED.change_percent,
                    buy_org_count = EXCLUDED.buy_org_count,
                    sell_org_count = EXCLUDED.sell_org_count,
                    org_buy_amount = EXCLUDED.org_buy_amount,
                    org_sell_amount = EXCLUDED.org_sell_amount,
                    org_net_amount = EXCLUDED.org_net_amount,
                    market_total_amount = EXCLUDED.market_total_amount,
                    org_net_amount_ratio = EXCLUDED.org_net_amount_ratio,
                    turnover_rate = EXCLUDED.turnover_rate,
                    free_market_cap = EXCLUDED.free_market_cap,
                    reason = EXCLUDED.reason,
                    source = EXCLUDED.source,
                    generated_at = EXCLUDED.generated_at,
                    collected_at = EXCLUDED.collected_at,
                    raw_payload = EXCLUDED.raw_payload
                """,
                rows,
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
                item.get("ai_summary"),
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
                    ai_summary,
                    provider,
                    upstream_source,
                    dedupe_key,
                    raw_payload
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb
                )
                ON CONFLICT (dedupe_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    content = EXCLUDED.content,
                    article_source = EXCLUDED.article_source,
                    published_at = EXCLUDED.published_at,
                    last_seen_at = EXCLUDED.last_seen_at,
                    source_url = EXCLUDED.source_url,
                    ai_summary = COALESCE(EXCLUDED.ai_summary, stock_news_item.ai_summary),
                    provider = EXCLUDED.provider,
                    upstream_source = EXCLUDED.upstream_source,
                    raw_payload = EXCLUDED.raw_payload
                """,
                rows,
            )

    async def update_news_ai_summaries(self, items: list[dict[str, str]]) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not items:
            return

        rows = [
            (
                item["dedupe_key"],
                item["ai_summary"],
            )
            for item in items
        ]

        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                UPDATE stock_news_item
                SET ai_summary = $2
                WHERE dedupe_key = $1
                  AND ai_summary IS DISTINCT FROM $2
                """,
                rows,
            )

    async def fetch_news_ai_summary_state(self, dedupe_keys: list[str]) -> dict[str, str | None]:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")
        if not dedupe_keys:
            return {}

        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT dedupe_key, ai_summary
                FROM stock_news_item
                WHERE dedupe_key = ANY($1::text[])
                """,
                dedupe_keys,
            )

        return {str(row["dedupe_key"]): row["ai_summary"] for row in rows}

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
