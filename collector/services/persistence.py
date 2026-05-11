from __future__ import annotations

import json

import asyncpg


class PostgresStore:
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

    async def persist_market_state(
        self,
        *,
        snapshot: dict[str, object],
        tick: dict[str, object],
        kline: dict[str, object],
    ) -> None:
        if self._pool is None:
            raise RuntimeError("PostgresStore must be connected before use")

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
                    tick["ts"],
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
                    tick["ts"],
                    json.dumps(snapshot),
                )

                await connection.execute(
                    """
                    INSERT INTO stock_tick (ts, symbol, price, volume, amount, side, source, raw)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    """,
                    tick["ts"],
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
                    kline["bucketTs"],
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
