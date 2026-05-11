from __future__ import annotations

import asyncio
import json
import logging
from time import monotonic
from collections.abc import Sequence

from redis.asyncio import Redis

from collector.services.market_simulator import build_market_state
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)


class CollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(settings.postgres_dsn)
        self._last_stream_id = "$"
        self._symbol_steps: dict[str, int] = {}
        self._last_collected_at: dict[str, float] = {}
        self._postgres_ready = False

    async def run(self) -> None:
        logger.info("collector worker started")

        while True:
            try:
                await self._ensure_postgres_connection()
                await self._consume_command_stream()
                await self._collect_active_symbols()
            except Exception:
                self._postgres_ready = False
                logger.exception("collector loop failed; retrying")
                await asyncio.sleep(self._settings.collector_poll_interval_seconds)

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return

        await self._postgres.connect()
        self._postgres_ready = True
        logger.info("collector connected to postgres")

    async def _collect_active_symbols(self) -> None:
        active_symbols = sorted(await self._redis.smembers(self._settings.active_symbols_key))
        logger.info("active symbols snapshot", extra={"active_symbols": active_symbols})

        for symbol in active_symbols:
            await self._collect_symbol(symbol)

    async def _collect_symbol(self, symbol: str) -> None:
        if not await self._redis.sismember(self._settings.active_symbols_key, symbol):
            self._clear_symbol_runtime_state(symbol)
            return

        now = monotonic()
        last_collected_at = self._last_collected_at.get(symbol)
        if last_collected_at is not None and now - last_collected_at < self._settings.collector_symbol_min_interval_seconds:
            return

        self._last_collected_at[symbol] = now
        step = self._symbol_steps.get(symbol, 0) + 1
        self._symbol_steps[symbol] = step

        market_state = build_market_state(symbol, step)
        await self._postgres.persist_market_state(
            snapshot=market_state["snapshot"],
            tick=market_state["tick"],
            kline=market_state["kline"],
        )

        await self._redis.set(
            f"{self._settings.market_snapshot_key_prefix}:{symbol}",
            json.dumps(market_state["snapshot"]),
        )
        await self._redis.set(
            f"{self._settings.market_event_key_prefix}:{symbol}",
            json.dumps(market_state["event"]),
        )
        await self._redis.xadd(
            self._settings.market_events_stream_key,
            {
                "symbol": symbol,
                "payload": json.dumps(market_state["event"]),
                "event": "market_update",
            },
        )

        logger.info(
            "collector persisted market state",
            extra={
                "symbol": symbol,
                "company_name": market_state["snapshot"]["companyName"],
                "last_price": market_state["snapshot"]["lastPrice"],
                "step": step,
            },
        )

    def _clear_symbol_runtime_state(self, symbol: str) -> None:
        self._symbol_steps.pop(symbol, None)
        self._last_collected_at.pop(symbol, None)

    async def _consume_command_stream(self) -> None:
        messages = await self._redis.xread(
            {self._settings.redis_stream_key: self._last_stream_id},
            count=10,
            block=self._settings.collector_poll_interval_seconds * 1000,
        )

        for _, entries in messages:
            await self._handle_entries(entries)

    async def _handle_entries(self, entries: Sequence[tuple[str, dict[str, str]]]) -> None:
        for entry_id, payload in entries:
            self._last_stream_id = entry_id
            logger.info(
                "collector received command",
                extra={
                    "entry_id": entry_id,
                    "payload": payload,
                },
            )

            if payload.get("event") == "activate_symbol":
                symbol = payload.get("symbol")
                if symbol:
                    await self._redis.sadd(self._settings.active_symbols_key, symbol)
                    return

            if payload.get("event") == "deactivate_symbol":
                symbol = payload.get("symbol")
                if symbol:
                    await self._redis.srem(self._settings.active_symbols_key, symbol)
                    await self._redis.delete(
                        f"{self._settings.market_snapshot_key_prefix}:{symbol}",
                        f"{self._settings.market_event_key_prefix}:{symbol}",
                    )
                    self._clear_symbol_runtime_state(symbol)
