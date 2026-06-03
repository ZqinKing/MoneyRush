from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from collector.services.gold_quote_client import GoldQuoteClient


logger = logging.getLogger(__name__)


class GoldDashboardCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._client = GoldQuoteClient(settings)

    async def run(self) -> None:
        if not self._settings.gold_dashboard_collector_enabled:
            logger.info("gold dashboard collector disabled")
            return

        logger.info("gold dashboard collector started")
        while True:
            payload: dict[str, object] | None = None
            previous_payload = await self._load_cached_payload()
            try:
                payload = await asyncio.to_thread(self._client.fetch, previous_payload)
            except Exception:
                logger.exception("gold dashboard refresh failed")

            if payload is None:
                payload = previous_payload

            if payload is None:
                payload = {
                    "generatedAt": datetime.now(UTC).isoformat(),
                    "isTradingSession": False,
                    "quotes": [],
                    "sources": {},
                    "degraded": True,
                }

            await self._redis.set(self._settings.gold_dashboard_cache_key, json.dumps(payload))
            await asyncio.sleep(self._client.next_refresh_seconds())

    async def _load_cached_payload(self) -> dict[str, object] | None:
        payload = await self._redis.get(self._settings.gold_dashboard_cache_key)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None
