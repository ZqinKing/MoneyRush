from __future__ import annotations

from datetime import UTC, datetime

from app.services.cache.redis_store import RedisStore


class MarketBroadcaster:
    def __init__(self, redis_store: RedisStore) -> None:
        self._redis_store = redis_store

    async def build_heartbeat_event(self) -> dict[str, object]:
        active_symbols = await self._redis_store.get_active_symbols()
        snapshots = await self._redis_store.get_symbol_snapshots(active_symbols)
        latest_events = await self._redis_store.get_symbol_events(active_symbols)

        return {
            "type": "market_state",
            "generatedAt": datetime.now(UTC).isoformat(),
            "activeSymbols": active_symbols,
            "snapshots": snapshots,
            "events": latest_events,
        }
