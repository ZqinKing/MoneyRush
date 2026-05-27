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
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return None
    return None


class FundQueryService:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            await self._ensure_runtime_schema()

    async def _ensure_runtime_schema(self) -> None:
        if self._pool is None:
            raise RuntimeError("FundQueryService must be connected before schema initialization")
        async with self._pool.acquire() as connection:
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
            await connection.execute("CREATE INDEX IF NOT EXISTS fund_command_log_fund_ts_idx ON fund_command_log (fund_code, ts DESC)")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def fetch_active_fund_snapshots(self, fund_codes: list[str]) -> dict[str, dict[str, object]]:
        if not fund_codes:
            return {}
        rows = await self._fetch(
            """
            SELECT fund_code, nav, daily_return, nav_date, estimated_intraday_return, updated_at, payload
            FROM fund_snapshot
            WHERE fund_code = ANY($1::text[])
            """,
            fund_codes,
        )
        snapshots: dict[str, dict[str, object]] = {}
        for row in rows:
            payload = dict(row["payload"] or {}) if isinstance(row["payload"], dict) else {}
            snapshots[str(row["fund_code"])] = {
                **payload,
                "fundCode": row["fund_code"],
                "nav": _to_float(row["nav"]),
                "dailyReturn": _to_float(row["daily_return"]),
                "navDate": _to_date_string(row["nav_date"]),
                "estimatedIntradayReturn": _to_float(row["estimated_intraday_return"]),
                "updatedAt": _to_iso(row["updated_at"]),
            }
        return snapshots

    async def fetch_fund_detail(self, fund_code: str) -> dict[str, object]:
        profile = await self._fetchrow(
            """
            SELECT fund_code, fund_name, fund_type, fund_company, manager_name, established_date, risk_level,
                   benchmark_index, management_fee, custody_fee, payload, created_at, updated_at
            FROM fund_profile
            WHERE fund_code = $1
            """,
            fund_code,
        )
        snapshot = await self._fetchrow(
            """
            SELECT fund_code, nav, daily_return, nav_date, estimated_intraday_return, updated_at, payload
            FROM fund_snapshot
            WHERE fund_code = $1
            """,
            fund_code,
        )
        nav_history = await self._fetch(
            """
            SELECT fund_code, nav_date, nav, accum_nav, daily_return, source, raw
            FROM fund_nav
            WHERE fund_code = $1
            ORDER BY nav_date DESC
            LIMIT 60
            """,
            fund_code,
        )
        top_holdings = await self._fetch(
            """
            SELECT fund_code, stock_symbol, stock_name, report_date, rank, weight_percent, hold_shares,
                   hold_market_value, change_type, raw
            FROM fund_stock_holding
            WHERE fund_code = $1
            ORDER BY report_date DESC, rank ASC NULLS LAST, stock_symbol ASC
            LIMIT 10
            """,
            fund_code,
        )
        holding_symbols = [str(row["stock_symbol"]) for row in top_holdings]
        stock_snapshots = await self._fetch_stock_snapshots(holding_symbols)
        return {
            "profile": self._serialize_profile(profile),
            "snapshot": self._serialize_snapshot(snapshot),
            "navHistory": [self._serialize_nav_row(row) for row in nav_history],
            "topHoldings": [self._serialize_holding_row(row) for row in top_holdings],
            "holdingStocksPerformance": self._build_holding_performance(top_holdings, stock_snapshots),
        }

    async def fetch_stock_funds(self, symbol: str) -> dict[str, object]:
        rows = await self._fetch(
            """
            SELECT stock_symbol, fund_code, fund_name, fund_type, report_date, weight_percent, hold_market_value,
                   change_type, raw
            FROM stock_fund_holding
            WHERE stock_symbol = $1
            ORDER BY report_date DESC, weight_percent DESC NULLS LAST, fund_code ASC
            LIMIT 40
            """,
            symbol,
        )
        latest_report_date = None
        if rows:
            latest_report_date = _to_date_string(rows[0]["report_date"])
        return {
            "symbol": symbol,
            "latestReportDate": latest_report_date,
            "items": [self._serialize_stock_fund_row(row) for row in rows],
        }

    async def upsert_fund_profile(self, *, fund_code: str, payload: dict[str, object]) -> None:
        if self._pool is None:
            raise RuntimeError("FundQueryService must be connected before use")
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
                fund_code,
                payload.get("fundName"),
                payload.get("fundType"),
                payload.get("fundCompany"),
                payload.get("managerName"),
                payload.get("establishedDate"),
                payload.get("riskLevel"),
                payload.get("benchmarkIndex"),
                payload.get("managementFee"),
                payload.get("custodyFee"),
                json.dumps(payload),
            )

    async def upsert_fund_snapshot(self, *, fund_code: str, payload: dict[str, object]) -> None:
        if self._pool is None:
            raise RuntimeError("FundQueryService must be connected before use")
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
                fund_code,
                payload.get("nav"),
                payload.get("dailyReturn"),
                date.fromisoformat(payload["navDate"]) if payload.get("navDate") else None,
                payload.get("estimatedIntradayReturn"),
                json.dumps(payload),
            )

    async def upsert_fund_nav_rows(self, rows: list[dict[str, object]]) -> None:
        if self._pool is None:
            raise RuntimeError("FundQueryService must be connected before use")
        if not rows:
            return
        values = [
            (
                row["fund_code"],
                row["nav_date"],
                row.get("nav"),
                row.get("accum_nav"),
                row.get("daily_return"),
                row.get("source", "akshare"),
                json.dumps(row.get("raw", {})),
            )
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
            raise RuntimeError("FundQueryService must be connected before use")
        if not rows:
            return
        values = [
            (
                row["fund_code"],
                row["stock_symbol"],
                row.get("stock_name"),
                row["report_date"],
                row.get("rank"),
                row.get("weight_percent"),
                row.get("hold_shares"),
                row.get("hold_market_value"),
                row.get("change_type"),
                json.dumps(row.get("raw", {})),
            )
            for row in rows
        ]
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO fund_stock_holding (
                    fund_code, stock_symbol, stock_name, report_date, rank, weight_percent, hold_shares,
                    hold_market_value, change_type, raw
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                ON CONFLICT (fund_code, stock_symbol, report_date) DO UPDATE SET
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
            raise RuntimeError("FundQueryService must be connected before use")
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
                json.dumps(row.get("raw", {})),
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
            raise RuntimeError("FundQueryService must be connected before use")
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

    async def persist_fund_command(self, *, timestamp: datetime, fund_code: str, command_type: str, payload: dict[str, object]) -> None:
        if self._pool is None:
            raise RuntimeError("FundQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO fund_command_log (ts, fund_code, command_type, payload)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                timestamp,
                fund_code,
                command_type,
                json.dumps(payload),
            )

    async def _fetch_stock_snapshots(self, symbols: list[str]) -> dict[str, dict[str, object]]:
        if not symbols:
            return {}
        rows = await self._fetch(
            """
            SELECT symbol, payload, updated_at
            FROM stock_snapshot
            WHERE symbol = ANY($1::text[])
            """,
            symbols,
        )
        snapshots: dict[str, dict[str, object]] = {}
        for row in rows:
            payload = dict(row["payload"] or {}) if isinstance(row["payload"], dict) else {}
            payload["updatedAt"] = _to_iso(row["updated_at"])
            snapshots[str(row["symbol"])] = payload
        return snapshots

    def _build_holding_performance(
        self,
        holdings: list[asyncpg.Record],
        stock_snapshots: dict[str, dict[str, object]],
    ) -> list[dict[str, object]]:
        performance: list[dict[str, object]] = []
        for row in holdings:
            symbol = str(row["stock_symbol"])
            snapshot = stock_snapshots.get(symbol) or {}
            last_price = _to_float(snapshot.get("lastPrice"))
            change_pct = _to_float(snapshot.get("changePct"))
            weight = _to_float(row["weight_percent"])
            performance.append(
                {
                    "stockSymbol": symbol,
                    "stockName": row["stock_name"],
                    "fundCode": row["fund_code"],
                    "rank": row["rank"],
                    "weightPercent": weight,
                    "lastPrice": last_price,
                    "changePct": change_pct,
                    "estimatedContribution": (weight or 0) * ((change_pct or 0) / 100),
                    "snapshotUpdatedAt": snapshot.get("updatedAt"),
                }
            )
        return performance

    def _serialize_profile(self, row: asyncpg.Record | None) -> dict[str, object] | None:
        if row is None:
            return None
        payload = dict(row["payload"] or {}) if isinstance(row["payload"], dict) else {}
        return {
            **payload,
            "fundCode": row["fund_code"],
            "fundName": row["fund_name"],
            "fundType": row["fund_type"],
            "fundCompany": row["fund_company"],
            "managerName": row["manager_name"],
            "establishedDate": _to_date_string(row["established_date"]),
            "riskLevel": row["risk_level"],
            "benchmarkIndex": row["benchmark_index"],
            "managementFee": _to_float(row["management_fee"]),
            "custodyFee": _to_float(row["custody_fee"]),
            "createdAt": _to_iso(row["created_at"]),
            "updatedAt": _to_iso(row["updated_at"]),
        }

    def _serialize_snapshot(self, row: asyncpg.Record | None) -> dict[str, object] | None:
        if row is None:
            return None
        payload = dict(row["payload"] or {}) if isinstance(row["payload"], dict) else {}
        return {
            **payload,
            "fundCode": row["fund_code"],
            "nav": _to_float(row["nav"]),
            "dailyReturn": _to_float(row["daily_return"]),
            "navDate": _to_date_string(row["nav_date"]),
            "estimatedIntradayReturn": _to_float(row["estimated_intraday_return"]),
            "updatedAt": _to_iso(row["updated_at"]),
        }

    def _serialize_nav_row(self, row: asyncpg.Record) -> dict[str, object]:
        raw = dict(row["raw"] or {}) if isinstance(row["raw"], dict) else {}
        return {
            **raw,
            "fundCode": row["fund_code"],
            "navDate": _to_date_string(row["nav_date"]),
            "nav": _to_float(row["nav"]),
            "accumNav": _to_float(row["accum_nav"]),
            "dailyReturn": _to_float(row["daily_return"]),
            "source": row["source"],
        }

    def _serialize_holding_row(self, row: asyncpg.Record) -> dict[str, object]:
        raw = dict(row["raw"] or {}) if isinstance(row["raw"], dict) else {}
        return {
            **raw,
            "fundCode": row["fund_code"],
            "stockSymbol": row["stock_symbol"],
            "stockName": row["stock_name"],
            "reportDate": _to_date_string(row["report_date"]),
            "rank": row["rank"],
            "weightPercent": _to_float(row["weight_percent"]),
            "holdShares": row["hold_shares"],
            "holdMarketValue": _to_float(row["hold_market_value"]),
            "changeType": row["change_type"],
        }

    def _serialize_stock_fund_row(self, row: asyncpg.Record) -> dict[str, object]:
        raw = dict(row["raw"] or {}) if isinstance(row["raw"], dict) else {}
        return {
            **raw,
            "stockSymbol": row["stock_symbol"],
            "fundCode": row["fund_code"],
            "fundName": row["fund_name"],
            "fundType": row["fund_type"],
            "reportDate": _to_date_string(row["report_date"]),
            "weightPercent": _to_float(row["weight_percent"]),
            "holdMarketValue": _to_float(row["hold_market_value"]),
            "changeType": row["change_type"],
        }

    async def _fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("FundQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, *args)

    async def _fetchrow(self, query: str, *args: object) -> asyncpg.Record | None:
        if self._pool is None:
            raise RuntimeError("FundQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetchrow(query, *args)
