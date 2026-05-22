from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from collector.services.market_overview_client import MarketOverviewClient


logger = logging.getLogger(__name__)


class MarketOverviewCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._client = MarketOverviewClient(settings)

    async def run(self) -> None:
        if not self._settings.market_overview_collector_enabled:
            logger.info("market overview collector disabled")
            return

        logger.info("market overview collector started")
        while True:
            payload: dict[str, object] | None = None
            try:
                payload = await asyncio.to_thread(self._client.fetch)
            except Exception:
                logger.exception("market overview refresh failed")

            if payload is None:
                cached_payload = await self._redis.get(self._settings.market_overview_cache_key)
                if cached_payload:
                    try:
                        payload = json.loads(cached_payload)
                    except json.JSONDecodeError:
                        payload = None

            if payload is None:
                payload = {
                    "generatedAt": datetime.now(UTC).isoformat(),
                    "marketStatus": "closed",
                    "isTradingSession": False,
                    "serverGeneratedAt": datetime.now(UTC).isoformat(),
                    "indexes": [],
                }

            if payload.get("breadth") is None:
                fallback_breadth = await self._build_active_symbol_breadth()
                if fallback_breadth is not None:
                    payload["breadth"] = fallback_breadth
                    payload["breadthFallback"] = True

            await self._redis.set(self._settings.market_overview_cache_key, json.dumps(payload))

            await asyncio.sleep(self._settings.market_overview_refresh_seconds)

    async def _build_active_symbol_breadth(self) -> dict[str, object] | None:
        active_symbols = sorted(await self._redis.smembers(self._settings.active_symbols_key))
        if not active_symbols:
            return None

        snapshot_keys = [f"{self._settings.market_snapshot_key_prefix}:{symbol}" for symbol in active_symbols]
        values = await self._redis.mget(snapshot_keys)

        rows: list[dict[str, object]] = []
        for symbol, value in zip(active_symbols, values, strict=False):
            if value is None:
                continue
            try:
                snapshot = json.loads(value)
            except json.JSONDecodeError:
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "最新价": snapshot.get("lastPrice"),
                    "涨跌幅": snapshot.get("changePct"),
                    "涨停价": snapshot.get("limitUp"),
                    "跌停价": snapshot.get("limitDown"),
                }
            )

        if not rows:
            return None

        advance_count = 0
        decline_count = 0
        flat_count = 0
        limit_up_count = 0
        limit_down_count = 0

        for row in rows:
            change_pct = row.get("涨跌幅")
            if isinstance(change_pct, (int, float)):
                if change_pct > 0:
                    advance_count += 1
                elif change_pct < 0:
                    decline_count += 1
                else:
                    flat_count += 1

            last_price = row.get("最新价")
            limit_up = row.get("涨停价")
            limit_down = row.get("跌停价")
            if isinstance(last_price, (int, float)) and isinstance(limit_up, (int, float)) and last_price >= limit_up:
                limit_up_count += 1
            if isinstance(last_price, (int, float)) and isinstance(limit_down, (int, float)) and last_price <= limit_down:
                limit_down_count += 1

        generated_at = datetime.now(UTC).isoformat()
        return {
            "advanceCount": advance_count,
            "declineCount": decline_count,
            "flatCount": flat_count,
            "limitUpCount": limit_up_count,
            "limitDownCount": limit_down_count,
            "sampleSize": len(rows),
            "updatedAt": generated_at,
            "source": "active-symbols-sample",
            "degraded": True,
        }
