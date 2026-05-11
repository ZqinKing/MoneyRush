from __future__ import annotations

import json
from datetime import UTC, datetime

from redis.asyncio import Redis


class RedisStore:
    def __init__(
        self,
        redis_url: str,
        stream_key: str,
        active_symbols_key: str,
        market_snapshot_key_prefix: str,
        market_event_key_prefix: str,
        market_events_stream_key: str,
    ) -> None:
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._stream_key = stream_key
        self._active_symbols_key = active_symbols_key
        self._market_snapshot_key_prefix = market_snapshot_key_prefix
        self._market_event_key_prefix = market_event_key_prefix
        self._market_events_stream_key = market_events_stream_key

    def _snapshot_key(self, symbol: str) -> str:
        return f"{self._market_snapshot_key_prefix}:{symbol}"

    def _event_key(self, symbol: str) -> str:
        return f"{self._market_event_key_prefix}:{symbol}"

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def activate_symbol(self, symbol: str) -> str:
        timestamp = datetime.now(UTC).isoformat()
        await self._redis.sadd(self._active_symbols_key, symbol)
        return await self._redis.xadd(
            self._stream_key,
            {
                "event": "activate_symbol",
                "symbol": symbol,
                "requested_at": timestamp,
            },
        )

    async def deactivate_symbol(self, symbol: str) -> str:
        timestamp = datetime.now(UTC).isoformat()
        await self._redis.srem(self._active_symbols_key, symbol)
        await self._redis.delete(self._snapshot_key(symbol), self._event_key(symbol))
        return await self._redis.xadd(
            self._stream_key,
            {
                "event": "deactivate_symbol",
                "symbol": symbol,
                "requested_at": timestamp,
            },
        )

    async def get_active_symbols(self) -> list[str]:
        symbols = await self._redis.smembers(self._active_symbols_key)
        return sorted(symbols)

    async def get_symbol_snapshot(self, symbol: str) -> dict[str, object] | None:
        payload = await self._redis.get(self._snapshot_key(symbol))
        if payload is None:
            return None
        return json.loads(payload)

    async def get_symbol_snapshots(self, symbols: list[str]) -> dict[str, dict[str, object]]:
        if not symbols:
            return {}

        values = await self._redis.mget([self._snapshot_key(symbol) for symbol in symbols])
        snapshots: dict[str, dict[str, object]] = {}
        for symbol, value in zip(symbols, values, strict=False):
            if value is not None:
                snapshots[symbol] = json.loads(value)
        return snapshots

    async def get_symbol_event(self, symbol: str) -> dict[str, object] | None:
        payload = await self._redis.get(self._event_key(symbol))
        if payload is None:
            return None
        return json.loads(payload)

    async def get_symbol_events(self, symbols: list[str]) -> dict[str, dict[str, object]]:
        if not symbols:
            return {}

        values = await self._redis.mget([self._event_key(symbol) for symbol in symbols])
        events: dict[str, dict[str, object]] = {}
        for symbol, value in zip(symbols, values, strict=False):
            if value is not None:
                events[symbol] = json.loads(value)
        return events

    async def set_symbol_snapshot(self, symbol: str, payload: dict[str, object]) -> None:
        await self._redis.set(self._snapshot_key(symbol), json.dumps(payload))

    async def set_symbol_event(self, symbol: str, payload: dict[str, object]) -> str:
        serialized = json.dumps(payload)
        await self._redis.set(self._event_key(symbol), serialized)
        return await self._redis.xadd(
            self._market_events_stream_key,
            {
                "symbol": symbol,
                "payload": serialized,
                "published_at": datetime.now(UTC).isoformat(),
            },
        )

    async def close(self) -> None:
        await self._redis.aclose()
