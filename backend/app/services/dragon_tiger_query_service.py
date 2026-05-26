from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (float, int, Decimal)):
        return float(value)
    return None


def _to_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        return int(value)
    return None


def _to_iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


class DragonTigerQueryService:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def fetch_daily_history(
        self,
        *,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        limit: int,
    ) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT trade_date, symbol, name, close_price, change_percent, net_buy_amount, buy_amount, sell_amount,
                   deal_amount, total_amount, net_buy_ratio, deal_amount_ratio, turnover_rate, free_market_cap,
                   explain, reason, after_1d, after_2d, after_5d, after_10d, source, generated_at, collected_at
            FROM dragon_tiger_daily_item
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::date IS NULL OR trade_date >= $2)
              AND ($3::date IS NULL OR trade_date <= $3)
            ORDER BY trade_date DESC, net_buy_amount DESC NULLS LAST, symbol ASC
            LIMIT $4
            """,
            symbol,
            start_date,
            end_date,
            limit,
        )
        return [
            {
                "tradeDate": row["trade_date"].isoformat() if isinstance(row["trade_date"], date) else None,
                "symbol": row["symbol"],
                "name": row["name"],
                "closePrice": _to_float(row["close_price"]),
                "changePercent": _to_float(row["change_percent"]),
                "netBuyAmount": _to_float(row["net_buy_amount"]),
                "buyAmount": _to_float(row["buy_amount"]),
                "sellAmount": _to_float(row["sell_amount"]),
                "dealAmount": _to_float(row["deal_amount"]),
                "totalAmount": _to_float(row["total_amount"]),
                "netBuyRatio": _to_float(row["net_buy_ratio"]),
                "dealAmountRatio": _to_float(row["deal_amount_ratio"]),
                "turnoverRate": _to_float(row["turnover_rate"]),
                "freeMarketCap": _to_float(row["free_market_cap"]),
                "explain": row["explain"],
                "reason": row["reason"],
                "after1d": _to_float(row["after_1d"]),
                "after2d": _to_float(row["after_2d"]),
                "after5d": _to_float(row["after_5d"]),
                "after10d": _to_float(row["after_10d"]),
                "source": row["source"],
                "generatedAt": _to_iso(row["generated_at"]),
                "collectedAt": _to_iso(row["collected_at"]),
            }
            for row in rows
        ]

    async def fetch_institution_history(
        self,
        *,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        limit: int,
    ) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT trade_date, symbol, name, close_price, change_percent, buy_org_count, sell_org_count,
                   org_buy_amount, org_sell_amount, org_net_amount, market_total_amount, org_net_amount_ratio,
                   turnover_rate, free_market_cap, reason, source, generated_at, collected_at
            FROM dragon_tiger_institution_item
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::date IS NULL OR trade_date >= $2)
              AND ($3::date IS NULL OR trade_date <= $3)
            ORDER BY trade_date DESC, org_net_amount DESC NULLS LAST, symbol ASC
            LIMIT $4
            """,
            symbol,
            start_date,
            end_date,
            limit,
        )
        return [
            {
                "tradeDate": row["trade_date"].isoformat() if isinstance(row["trade_date"], date) else None,
                "symbol": row["symbol"],
                "name": row["name"],
                "closePrice": _to_float(row["close_price"]),
                "changePercent": _to_float(row["change_percent"]),
                "buyOrgCount": _to_int(row["buy_org_count"]),
                "sellOrgCount": _to_int(row["sell_org_count"]),
                "orgBuyAmount": _to_float(row["org_buy_amount"]),
                "orgSellAmount": _to_float(row["org_sell_amount"]),
                "orgNetAmount": _to_float(row["org_net_amount"]),
                "marketTotalAmount": _to_float(row["market_total_amount"]),
                "orgNetAmountRatio": _to_float(row["org_net_amount_ratio"]),
                "turnoverRate": _to_float(row["turnover_rate"]),
                "freeMarketCap": _to_float(row["free_market_cap"]),
                "reason": row["reason"],
                "source": row["source"],
                "generatedAt": _to_iso(row["generated_at"]),
                "collectedAt": _to_iso(row["collected_at"]),
            }
            for row in rows
        ]

    async def fetch_history_summary(self) -> dict[str, object]:
        row = await self._fetchrow(
            """
            WITH daily AS (
                SELECT COUNT(*) AS count, MAX(trade_date) AS latest_trade_date FROM dragon_tiger_daily_item
            ), institution AS (
                SELECT COUNT(*) AS count, MAX(trade_date) AS latest_trade_date FROM dragon_tiger_institution_item
            )
            SELECT
                (SELECT count FROM daily) AS daily_count,
                (SELECT latest_trade_date FROM daily) AS daily_latest_trade_date,
                (SELECT count FROM institution) AS institution_count,
                (SELECT latest_trade_date FROM institution) AS institution_latest_trade_date
            """
        )
        if row is None:
            return {"daily": {"count": 0, "latestTradeDate": None}, "institution": {"count": 0, "latestTradeDate": None}}
        return {
            "daily": {
                "count": int(row["daily_count"] or 0),
                "latestTradeDate": row["daily_latest_trade_date"].isoformat() if isinstance(row["daily_latest_trade_date"], date) else None,
            },
            "institution": {
                "count": int(row["institution_count"] or 0),
                "latestTradeDate": row["institution_latest_trade_date"].isoformat() if isinstance(row["institution_latest_trade_date"], date) else None,
            },
        }

    async def _fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("DragonTigerQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, *args)

    async def _fetchrow(self, query: str, *args: object) -> asyncpg.Record | None:
        if self._pool is None:
            raise RuntimeError("DragonTigerQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetchrow(query, *args)
