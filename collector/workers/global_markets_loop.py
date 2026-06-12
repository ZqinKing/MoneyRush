from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from collector.services.global_markets_client import GlobalMarketsClient


logger = logging.getLogger(__name__)


class GlobalMarketsCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._client = GlobalMarketsClient(settings)

    async def run(self, *, run_once: bool = False) -> None:
        if not self._settings.global_markets_collector_enabled:
            logger.info("global markets collector disabled")
            return

        logger.info("global markets collector started")
        while True:
            await self.refresh_once()
            if run_once:
                return
            await asyncio.sleep(self._settings.global_markets_refresh_seconds)

    async def refresh_once(self) -> dict[str, object]:
        previous_payload = await self._load_cached_payload()
        payload: dict[str, object] | None = None
        try:
            payload = await asyncio.to_thread(self._client.fetch, previous_payload)
        except Exception as exc:
            logger.exception("global markets refresh failed")
            if previous_payload is not None:
                payload = self._mark_last_good_stale(previous_payload, exc)

        if payload is None:
            payload = {
                "items": [],
                "regions": [],
                "source": "unavailable",
                "updatedAt": datetime.now(UTC).isoformat(),
                "delayLabel": None,
                "stale": True,
                "errors": [{"source": "worker", "marketId": None, "message": "no global market payload available"}],
            }

        await self._redis.set(self._settings.global_markets_cache_key, json.dumps(payload), ex=120)
        return payload

    async def _load_cached_payload(self) -> dict[str, object] | None:
        payload = await self._redis.get(self._settings.global_markets_cache_key)
        if payload is None:
            return None
        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None

    def _mark_last_good_stale(self, payload: dict[str, object], exc: Exception) -> dict[str, object]:
        recovered = dict(payload)
        recovered["stale"] = True
        recovered["updatedAt"] = datetime.now(UTC).isoformat()
        errors = recovered.get("errors")
        if not isinstance(errors, list):
            errors = []
        recovered["errors"] = [
            *errors,
            {"source": "worker", "marketId": None, "message": str(exc) or exc.__class__.__name__},
        ]
        items = recovered.get("items")
        if isinstance(items, list):
            recovered["items"] = [dict(item, stale=True) if isinstance(item, dict) else item for item in items]
        regions = recovered.get("regions")
        if isinstance(regions, list):
            recovered["regions"] = [dict(region, stale=True) if isinstance(region, dict) else region for region in regions]
        return recovered
