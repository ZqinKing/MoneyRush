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


def _coerce_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _days_since(value: object) -> int | None:
    target_date = _coerce_date(value)
    if target_date is None:
        return None
    return max((datetime.now(UTC).date() - target_date).days, 0)


def _clamp_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return round(min(max(value, 0.0), 100.0), 2)


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_qdii_fund_type(value: object) -> bool:
    text = _normalize_text(value)
    return bool(text and "qdii" in text.lower())


def _risk_signal(*, kind: str, severity: str, title: str, message: str) -> dict[str, str]:
    return {
        "kind": kind,
        "severity": severity,
        "title": title,
        "message": message,
    }


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


def _coerce_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _clamp_percent(value: object) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    return round(min(max(number, 0.0), 100.0), 2)


def _days_since(value: object) -> int | None:
    target_date = _coerce_date(value)
    if target_date is None:
        return None
    return max((datetime.now(UTC).date() - target_date).days, 0)


def _is_qdii_fund_type(value: object) -> bool:
    text = str(value or "").strip().lower()
    return "qdii" in text


def _risk_signal(kind: str, severity: str, title: str, message: str) -> dict[str, str]:
    return {
        "kind": kind,
        "severity": severity,
        "title": title,
        "message": message,
    }


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
            payload = _decode_jsonish(row["payload"])
            snapshots[str(row["fund_code"])] = {
                **payload,
                "fundCode": row["fund_code"],
                "nav": _to_float(row["nav"]),
                "dailyReturn": _to_float(row["daily_return"]),
                "navDate": _to_date_string(row["nav_date"]),
                "estimatedIntradayReturn": _to_float(row["estimated_intraday_return"]),
                "updatedAt": _to_iso(row["updated_at"]),
            }
        latest_holding_rows = await self._fetch_latest_top_holding_rows_for_funds(fund_codes)
        transparency_by_fund = self._build_transparency_map_from_rows(latest_holding_rows)
        estimated_returns = await self._compute_estimated_returns_from_rows(latest_holding_rows)
        for fund_code, estimated_return in estimated_returns.items():
            snapshot = snapshots.get(fund_code)
            if snapshot is not None:
                snapshot["estimatedIntradayReturn"] = estimated_return
        for fund_code in fund_codes:
            snapshot = snapshots.setdefault(fund_code, {"fundCode": fund_code})
            if fund_code in estimated_returns:
                snapshot["estimatedIntradayReturn"] = estimated_returns[fund_code]
            transparency = transparency_by_fund.get(fund_code) or self._build_empty_transparency(
                fund_type=snapshot.get("fundType"),
            )
            snapshot["transparency"] = transparency
            snapshot["riskSignals"] = self._build_fund_risk_signals(
                transparency=transparency,
                fund_type=snapshot.get("fundType"),
            )
            snapshot["riskFlags"] = [signal["kind"] for signal in snapshot["riskSignals"]]
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
        profile_payload = self._serialize_profile(profile)
        top_holdings = await self._fetch_latest_top_holding_rows_for_funds([fund_code])
        holding_symbols = [str(row["stock_symbol"]) for row in top_holdings]
        stock_snapshots = await self._fetch_stock_snapshots(holding_symbols)
        holding_performance = self._build_holding_performance(top_holdings, stock_snapshots)
        estimated_intraday_return = self._sum_estimated_contributions(holding_performance)
        serialized_snapshot = self._serialize_snapshot(snapshot)
        if serialized_snapshot is not None:
            serialized_snapshot["estimatedIntradayReturn"] = estimated_intraday_return
        transparency = self._build_transparency_map_from_rows(top_holdings).get(fund_code) or self._build_empty_transparency(
            fund_type=(profile_payload or {}).get("fundType"),
        )
        risk_signals = self._build_fund_risk_signals(
            transparency=transparency,
            fund_type=(profile_payload or {}).get("fundType"),
        )
        if serialized_snapshot is not None:
            serialized_snapshot["transparency"] = transparency
            serialized_snapshot["riskSignals"] = risk_signals
            serialized_snapshot["riskFlags"] = [signal["kind"] for signal in risk_signals]
        return {
            "profile": profile_payload,
            "snapshot": serialized_snapshot,
            "navHistory": [self._serialize_nav_row(row) for row in nav_history],
            "topHoldings": [self._serialize_holding_row(row) for row in top_holdings],
            "holdingStocksPerformance": holding_performance,
            "transparency": transparency,
            "riskSignals": risk_signals,
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

    async def fetch_active_fund_portfolio_view(self, fund_codes: list[str]) -> dict[str, object]:
        normalized_fund_codes = sorted({str(code).strip() for code in fund_codes if str(code).strip()})
        profile_meta = await self._fetch_fund_profile_meta(normalized_fund_codes)
        active_fund_count = len(normalized_fund_codes)
        assumptions = {
            "weightingMethod": "equal_weight_synced_funds",
            "weightingLabel": "当前按已完成持仓同步的激活基金等权估算",
            "disclosureBasis": "仅基于最新披露重仓进行观察",
            "exposureBasis": "单股组合估算权重 = 所有已同步基金持仓权重 / 已同步基金数量之和",
            "note": "当前监控组合视角只纳入已同步披露持仓的基金，不代表你的真实持仓比例。",
        }
        if not normalized_fund_codes:
            return {
                "status": "no_active_funds",
                "statusMessage": "当前没有激活基金，暂时无法生成监控组合视角。",
                "assumptions": assumptions,
                "summary": {
                    "activeFundCount": 0,
                    "participatingFundCount": 0,
                    "pendingFundCount": 0,
                    "fundsWithHoldingsCount": 0,
                    "stockExposureCount": 0,
                    "repeatedHoldingCount": 0,
                    "top1ExposurePercent": None,
                    "top3ExposurePercent": None,
                    "qdiiFundCount": 0,
                    "qdiiFundRatio": 0,
                    "latestReportDate": None,
                    "oldestReportDate": None,
                    "staleFundCount": 0,
                },
                "funds": [],
                "stockExposure": [],
                "repeatedHoldings": [],
                "riskSignals": [
                    _risk_signal(
                        kind="no_active_funds",
                        severity="info",
                        title="暂无激活基金",
                        message="先在基金列表中激活基金，才能形成监控组合视角。",
                    )
                ],
            }

        latest_holding_rows = await self._fetch_latest_top_holding_rows_for_funds(normalized_fund_codes)
        transparency_by_fund = self._build_transparency_map_from_rows(latest_holding_rows)
        funds = [
            {
                "fundCode": fund_code,
                "fundName": (profile_meta.get(fund_code) or {}).get("fundName") or fund_code,
                "fundType": (profile_meta.get(fund_code) or {}).get("fundType"),
                "transparency": transparency_by_fund.get(fund_code) or self._build_empty_transparency(
                    fund_type=(profile_meta.get(fund_code) or {}).get("fundType"),
                ),
            }
            for fund_code in normalized_fund_codes
        ]

        if not latest_holding_rows:
            qdii_fund_count = sum(1 for fund in funds if _is_qdii_fund_type(fund["fundType"]))
            qdii_fund_ratio = round(qdii_fund_count / active_fund_count, 4) if active_fund_count else 0
            return {
                "status": "waiting_for_holdings",
                "statusMessage": "已激活基金尚未完成披露持仓同步，组合穿透稍后可用。",
                "assumptions": assumptions,
                "summary": {
                    "activeFundCount": active_fund_count,
                    "participatingFundCount": 0,
                    "pendingFundCount": active_fund_count,
                    "fundsWithHoldingsCount": 0,
                    "stockExposureCount": 0,
                    "repeatedHoldingCount": 0,
                    "top1ExposurePercent": None,
                    "top3ExposurePercent": None,
                    "qdiiFundCount": qdii_fund_count,
                    "qdiiFundRatio": qdii_fund_ratio,
                    "latestReportDate": None,
                    "oldestReportDate": None,
                    "staleFundCount": 0,
                },
                "funds": funds,
                "stockExposure": [],
                "repeatedHoldings": [],
                "riskSignals": [
                    _risk_signal(
                        kind="waiting_for_holdings",
                        severity="info",
                        title="披露持仓待同步",
                        message="当前观察池还没有可用的最新披露重仓，暂时无法给出组合穿透结果。",
                    )
                ],
            }

        participating_fund_count = len(transparency_by_fund)
        denominator = max(participating_fund_count, 1)
        stock_snapshots = await self._fetch_stock_snapshots(sorted({str(row["stock_symbol"]) for row in latest_holding_rows}))
        exposure_map: dict[str, dict[str, object]] = {}
        for row in latest_holding_rows:
            stock_symbol = str(row["stock_symbol"])
            stock_snapshot = stock_snapshots.get(stock_symbol) or {}
            stock_name = row["stock_name"] or stock_symbol
            fund_code = str(row["fund_code"])
            estimated_exposure = (_to_float(row["weight_percent"]) or 0.0) / denominator
            exposure = exposure_map.setdefault(
                stock_symbol,
                {
                    "stockSymbol": stock_symbol,
                    "stockName": stock_name,
                    "stockMarket": row["stock_market"],
                    "estimatedBasketExposurePercent": 0.0,
                    "contributingFundCount": 0,
                    "contributingFunds": [],
                    "latestReportDate": None,
                    "lastPrice": _to_float(stock_snapshot.get("lastPrice")),
                    "changePct": _to_float(stock_snapshot.get("changePct")),
                    "snapshotUpdatedAt": stock_snapshot.get("updatedAt"),
                },
            )
            exposure["estimatedBasketExposurePercent"] = float(exposure["estimatedBasketExposurePercent"] or 0.0) + estimated_exposure
            exposure["contributingFunds"].append(
                {
                    "fundCode": fund_code,
                    "fundName": row["fund_name"] or (profile_meta.get(fund_code) or {}).get("fundName") or fund_code,
                    "fundType": row["fund_type"] or (profile_meta.get(fund_code) or {}).get("fundType"),
                    "weightPercent": _to_float(row["weight_percent"]),
                    "holdMarketValue": _to_float(row["hold_market_value"]),
                    "reportDate": _to_date_string(row["report_date"]),
                }
            )
            latest_report_date = _to_date_string(row["report_date"])
            current_report_date = exposure.get("latestReportDate")
            exposure["latestReportDate"] = max(filter(None, [current_report_date, latest_report_date]), default=None)

        stock_exposure: list[dict[str, object]] = []
        for exposure in exposure_map.values():
            contributing_funds = sorted(
                exposure["contributingFunds"],
                key=lambda item: (
                    -(_to_float(item.get("weightPercent")) or 0.0),
                    str(item.get("fundCode") or ""),
                ),
            )
            exposure["contributingFunds"] = contributing_funds
            exposure["contributingFundCount"] = len(contributing_funds)
            estimated_basket_exposure = round(float(exposure["estimatedBasketExposurePercent"] or 0.0), 4)
            change_pct = _to_float(exposure.get("changePct"))
            exposure["estimatedBasketExposurePercent"] = estimated_basket_exposure
            exposure["estimatedContribution"] = round(estimated_basket_exposure * ((change_pct or 0.0) / 100), 4)
            exposure["stressImpactDown1Pct"] = round(-(estimated_basket_exposure / 100), 4)
            exposure["stressImpactDown5Pct"] = round(-((estimated_basket_exposure / 100) * 5), 4)
            stock_exposure.append(exposure)

        stock_exposure.sort(
            key=lambda item: (
                -(item.get("estimatedBasketExposurePercent") or 0.0),
                -(item.get("contributingFundCount") or 0),
                str(item.get("stockSymbol") or ""),
            )
        )
        repeated_holdings = [item for item in stock_exposure if (item.get("contributingFundCount") or 0) >= 2]
        fund_transparency_rows = [fund["transparency"] for fund in funds if fund.get("transparency", {}).get("status") == "ready"]
        freshness_values = [item.get("freshnessDays") for item in fund_transparency_rows if isinstance(item.get("freshnessDays"), int)]
        report_dates = [item.get("latestReportDate") for item in fund_transparency_rows if item.get("latestReportDate")]
        qdii_fund_count = sum(1 for fund in funds if _is_qdii_fund_type(fund.get("fundType")))
        qdii_fund_ratio = round(qdii_fund_count / active_fund_count, 4) if active_fund_count else 0
        top1_exposure = stock_exposure[0]["estimatedBasketExposurePercent"] if stock_exposure else None
        top3_exposure = round(sum(item.get("estimatedBasketExposurePercent") or 0.0 for item in stock_exposure[:3]), 4) if stock_exposure else None
        summary = {
            "activeFundCount": active_fund_count,
            "participatingFundCount": participating_fund_count,
            "pendingFundCount": max(active_fund_count - participating_fund_count, 0),
            "fundsWithHoldingsCount": len(fund_transparency_rows),
            "stockExposureCount": len(stock_exposure),
            "repeatedHoldingCount": len(repeated_holdings),
            "top1ExposurePercent": top1_exposure,
            "top3ExposurePercent": top3_exposure,
            "qdiiFundCount": qdii_fund_count,
            "qdiiFundRatio": qdii_fund_ratio,
            "latestReportDate": max(report_dates) if report_dates else None,
            "oldestReportDate": min(report_dates) if report_dates else None,
            "staleFundCount": sum(1 for value in freshness_values if value >= 45),
            "maxFreshnessDays": max(freshness_values) if freshness_values else None,
        }
        return {
            "status": "partial_holdings" if summary["pendingFundCount"] > 0 else "ready",
            "statusMessage": (
                "部分激活基金尚未完成持仓同步，当前只对已同步基金进行等权估算。"
                if summary["pendingFundCount"] > 0
                else None
            ),
            "assumptions": assumptions,
            "summary": summary,
            "funds": funds,
            "stockExposure": stock_exposure,
            "repeatedHoldings": repeated_holdings[:10],
            "riskSignals": self._build_portfolio_risk_signals(
                status="partial_holdings" if summary["pendingFundCount"] > 0 else "ready",
                summary=summary,
                repeated_holdings=repeated_holdings,
            ),
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
                row.get("stock_market"),
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
            payload = _decode_jsonish(row["payload"])
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

    def _sum_estimated_contributions(self, performance: list[dict[str, object]]) -> float | None:
        values = [item.get("estimatedContribution") for item in performance if isinstance(item.get("estimatedContribution"), (float, int, Decimal))]
        if not values:
            return None
        return float(sum(values))

    async def _compute_estimated_returns_for_funds(self, fund_codes: list[str]) -> dict[str, float | None]:
        if not fund_codes:
            return {}
        rows = await self._fetch_latest_top_holding_rows_for_funds(fund_codes)
        return await self._compute_estimated_returns_from_rows(rows)

    async def _compute_estimated_returns_from_rows(self, rows: list[asyncpg.Record]) -> dict[str, float | None]:
        holdings_by_fund: dict[str, list[asyncpg.Record]] = {}
        symbols: list[str] = []
        for row in rows:
            fund_code = str(row["fund_code"])
            holdings_by_fund.setdefault(fund_code, []).append(row)
            symbols.append(str(row["stock_symbol"]))
        stock_snapshots = await self._fetch_stock_snapshots(sorted(set(symbols)))
        estimated_returns: dict[str, float | None] = {}
        for fund_code, holdings in holdings_by_fund.items():
            performance = self._build_holding_performance(holdings, stock_snapshots)
            estimated_returns[fund_code] = self._sum_estimated_contributions(performance)
        return estimated_returns

    async def _fetch_latest_top_holding_rows_for_funds(self, fund_codes: list[str]) -> list[asyncpg.Record]:
        if not fund_codes:
            return []
        return await self._fetch(
            """
            WITH latest_report AS (
                SELECT fund_code, MAX(report_date) AS report_date
                FROM fund_stock_holding
                WHERE fund_code = ANY($1::text[])
                GROUP BY fund_code
            ), ranked AS (
                SELECT holding.fund_code, profile.fund_name, profile.fund_type, holding.stock_symbol, holding.stock_market,
                       holding.stock_name, holding.report_date, holding.rank, holding.weight_percent, holding.hold_shares,
                       holding.hold_market_value, holding.change_type, holding.raw,
                       ROW_NUMBER() OVER (
                            PARTITION BY holding.fund_code, holding.rank
                            ORDER BY holding.weight_percent DESC NULLS LAST, holding.hold_market_value DESC NULLS LAST, holding.stock_symbol ASC
                        ) AS rank_choice
                FROM fund_stock_holding AS holding
                INNER JOIN latest_report
                    ON latest_report.fund_code = holding.fund_code
                   AND latest_report.report_date = holding.report_date
                LEFT JOIN fund_profile AS profile
                    ON profile.fund_code = holding.fund_code
            )
            SELECT fund_code, fund_name, fund_type, stock_symbol, stock_market, stock_name, report_date, rank,
                   weight_percent, hold_shares, hold_market_value, change_type, raw
            FROM ranked
            WHERE rank_choice = 1
            ORDER BY fund_code ASC, rank ASC NULLS LAST, stock_symbol ASC
            """,
            fund_codes,
        )

    async def _fetch_fund_profile_meta(self, fund_codes: list[str]) -> dict[str, dict[str, object]]:
        if not fund_codes:
            return {}
        rows = await self._fetch(
            """
            SELECT fund_code, fund_name, fund_type
            FROM fund_profile
            WHERE fund_code = ANY($1::text[])
            """,
            fund_codes,
        )
        return {
            str(row["fund_code"]): {
                "fundName": row["fund_name"],
                "fundType": row["fund_type"],
            }
            for row in rows
        }

    def _build_empty_transparency(self, *, fund_type: object = None) -> dict[str, object]:
        return {
            "status": "waiting_for_holdings",
            "latestReportDate": None,
            "freshnessDays": None,
            "disclosedWeightPercent": None,
            "undisclosedWeightPercent": None,
            "holdingCount": 0,
            "top1WeightPercent": None,
            "top3WeightPercent": None,
            "isQdii": _is_qdii_fund_type(fund_type),
        }

    def _build_transparency_map_from_rows(self, rows: list[asyncpg.Record]) -> dict[str, dict[str, object]]:
        holdings_by_fund: dict[str, list[asyncpg.Record]] = {}
        for row in rows:
            holdings_by_fund.setdefault(str(row["fund_code"]), []).append(row)
        transparency_by_fund: dict[str, dict[str, object]] = {}
        for fund_code, holdings in holdings_by_fund.items():
            report_date = holdings[0]["report_date"] if holdings else None
            weights = [_to_float(row["weight_percent"]) for row in holdings]
            disclosed_weight_percent = _clamp_percent(sum(value for value in weights if value is not None))
            top1_weight = next((_to_float(row["weight_percent"]) for row in holdings if row["rank"] == 1), None)
            top3_weight = _clamp_percent(sum((_to_float(row["weight_percent"]) or 0.0) for row in holdings if isinstance(row["rank"], int) and row["rank"] <= 3))
            fund_type = holdings[0]["fund_type"] if holdings else None
            transparency_by_fund[fund_code] = {
                "status": "ready",
                "latestReportDate": _to_date_string(report_date),
                "freshnessDays": _days_since(report_date),
                "disclosedWeightPercent": disclosed_weight_percent,
                "undisclosedWeightPercent": _clamp_percent(100.0 - disclosed_weight_percent) if disclosed_weight_percent is not None else None,
                "holdingCount": len(holdings),
                "top1WeightPercent": _clamp_percent(top1_weight),
                "top3WeightPercent": top3_weight,
                "isQdii": _is_qdii_fund_type(fund_type),
            }
        return transparency_by_fund

    def _build_fund_risk_signals(self, *, transparency: dict[str, object], fund_type: object = None) -> list[dict[str, str]]:
        if transparency.get("status") != "ready":
            return [
                _risk_signal(
                    kind="waiting_for_holdings",
                    severity="info",
                    title="披露持仓待同步",
                    message="当前暂无可用的最新披露重仓数据，估算提示暂不完整。",
                )
            ]
        signals: list[dict[str, str]] = []
        freshness_days = transparency.get("freshnessDays")
        top1_weight_percent = transparency.get("top1WeightPercent")
        top3_weight_percent = transparency.get("top3WeightPercent")
        disclosed_weight_percent = transparency.get("disclosedWeightPercent")
        latest_report_date = transparency.get("latestReportDate") or "未知报告期"
        if isinstance(freshness_days, int) and freshness_days >= 45:
            signals.append(
                _risk_signal(
                    kind="stale_disclosure",
                    severity="warning",
                    title="披露数据滞后",
                    message=f"最新披露报告期为 {latest_report_date}，距今约 {freshness_days} 天。",
                )
            )
        if isinstance(top1_weight_percent, (int, float)) and top1_weight_percent >= 5:
            signals.append(
                _risk_signal(
                    kind="high_top1_concentration",
                    severity="warning",
                    title="头号重仓集中",
                    message=f"Top1 重仓约 {top1_weight_percent:.2f}% ，需关注单股波动对基金估算联动的放大效应。",
                )
            )
        if isinstance(top3_weight_percent, (int, float)) and top3_weight_percent >= 15:
            signals.append(
                _risk_signal(
                    kind="high_top3_concentration",
                    severity="warning",
                    title="前 3 重仓占比较高",
                    message=f"前 3 重仓合计约 {top3_weight_percent:.2f}% ，组合波动可能更受少数股票影响。",
                )
            )
        if isinstance(disclosed_weight_percent, (int, float)) and disclosed_weight_percent < 50:
            signals.append(
                _risk_signal(
                    kind="low_disclosure_coverage",
                    severity="info",
                    title="未披露部分较多",
                    message=f"当前已披露重仓约 {disclosed_weight_percent:.2f}% ，剩余部分仍可能影响实际净值变化。",
                )
            )
        if transparency.get("isQdii") or _is_qdii_fund_type(fund_type):
            signals.append(
                _risk_signal(
                    kind="qdii_exposure",
                    severity="info",
                    title="QDII / 跨市场基金",
                    message="跨市场基金需额外关注时差、汇率和海外市场开收盘节奏。",
                )
            )
        return signals

    def _build_portfolio_risk_signals(
        self,
        *,
        status: str,
        summary: dict[str, object],
        repeated_holdings: list[dict[str, object]],
    ) -> list[dict[str, str]]:
        signals: list[dict[str, str]] = []
        repeated_holding_count = int(summary.get("repeatedHoldingCount") or 0)
        pending_fund_count = int(summary.get("pendingFundCount") or 0)
        top1_exposure_percent = _to_float(summary.get("top1ExposurePercent"))
        top3_exposure_percent = _to_float(summary.get("top3ExposurePercent"))
        stale_fund_count = int(summary.get("staleFundCount") or 0)
        qdii_fund_ratio = _to_float(summary.get("qdiiFundRatio"))
        if status == "partial_holdings" and pending_fund_count > 0:
            signals.append(
                _risk_signal(
                    kind="pending_holdings_sync",
                    severity="info",
                    title="部分基金尚未同步持仓",
                    message=f"当前仍有 {pending_fund_count} 只激活基金尚未完成持仓同步，本页只按已同步基金等权估算。",
                )
            )
        if repeated_holding_count > 0:
            top_repeated = repeated_holdings[0] if repeated_holdings else None
            repeated_name = top_repeated.get("stockName") if isinstance(top_repeated, dict) else None
            repeated_count = top_repeated.get("contributingFundCount") if isinstance(top_repeated, dict) else None
            signals.append(
                _risk_signal(
                    kind="repeated_holdings",
                    severity="warning",
                    title="存在重复持仓",
                    message=(
                        f"当前观察池有 {repeated_holding_count} 只股票被多只基金共同持有，"
                        f"其中 {repeated_name or '头号重复股'} 被约 {repeated_count or '--'} 只基金共同持有。"
                    ),
                )
            )
        if isinstance(top1_exposure_percent, (int, float)) and top1_exposure_percent >= 5:
            signals.append(
                _risk_signal(
                    kind="top1_exposure_concentration",
                    severity="warning",
                    title="头号单股暴露偏高",
                    message=f"监控组合 top1 单股估算暴露约 {top1_exposure_percent:.2f}% ，需关注单股波动放大。",
                )
            )
        if isinstance(top3_exposure_percent, (int, float)) and top3_exposure_percent >= 15:
            signals.append(
                _risk_signal(
                    kind="top3_exposure_concentration",
                    severity="warning",
                    title="前 3 单股暴露偏高",
                    message=f"监控组合前 3 单股合计暴露约 {top3_exposure_percent:.2f}% ，集中度偏高。",
                )
            )
        if stale_fund_count > 0:
            signals.append(
                _risk_signal(
                    kind="stale_disclosure",
                    severity="warning",
                    title="存在较旧披露数据",
                    message=f"当前有 {stale_fund_count} 只基金的最近披露已明显滞后，组合观察需保留保守口径。",
                )
            )
        if isinstance(qdii_fund_ratio, (int, float)) and qdii_fund_ratio >= 0.4:
            signals.append(
                _risk_signal(
                    kind="qdii_exposure",
                    severity="info",
                    title="QDII 暴露较高",
                    message=f"当前观察池中 QDII 基金占比约 {(qdii_fund_ratio * 100):.0f}% ，需额外关注跨市场时差与汇率影响。",
                )
            )
        if not signals:
            signals.append(
                _risk_signal(
                    kind="baseline_watch",
                    severity="info",
                    title="结构风险可持续观察",
                    message="当前未触发明显结构性阈值，但组合观察仍受披露时滞与未披露持仓影响。",
                )
            )
        return signals

    def _serialize_profile(self, row: asyncpg.Record | None) -> dict[str, object] | None:
        if row is None:
            return None
        payload = _decode_jsonish(row["payload"])
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
        payload = _decode_jsonish(row["payload"])
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
            "stockMarket": row.get("stock_market"),
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
