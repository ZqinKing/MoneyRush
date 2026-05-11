from __future__ import annotations

import asyncio
import json
import logging
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

    async def run(self) -> None:
        logger.info("collector worker started")
        await self._postgres.connect()

        while True:
            try:
                await self._consume_command_stream()
                await self._collect_active_symbols()
            except Exception:
                logger.exception("collector loop failed; retrying")
                await asyncio.sleep(self._settings.collector_poll_interval_seconds)

    async def _collect_active_symbols(self) -> None:
        active_symbols = sorted(await self._redis.smembers(self._settings.active_symbols_key))
        logger.info("active symbols snapshot", extra={"active_symbols": active_symbols})

        for symbol in active_symbols:
            await self._collect_symbol(symbol)

    async def _collect_symbol(self, symbol: str) -> None:
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
                "last_price": market_state["snapshot"]["lastPrice"],
                "step": step,
            },
        )

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
