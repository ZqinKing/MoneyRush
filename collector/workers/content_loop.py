from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from collector.services.akshare_content_client import AkshareContentClient
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)


class ContentCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = AkshareContentClient(settings)
        self._postgres_ready = False

    async def run(self) -> None:
        if not self._settings.content_collector_enabled:
            logger.info("content collector disabled")
            return

        logger.info("content collector worker started")
        while True:
            try:
                await self._ensure_postgres_connection()
                await self._ensure_default_jobs()
                did_work = await self._run_once()
                if not did_work:
                    await asyncio.sleep(self._settings.content_collector_poll_interval_seconds)
            except Exception:
                self._postgres_ready = False
                logger.exception("content collector loop failed; retrying")
                await asyncio.sleep(self._settings.content_collector_poll_interval_seconds)

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect()
        self._postgres_ready = True
        logger.info("content collector connected to postgres")

    async def _ensure_default_jobs(self) -> None:
        active_symbols = sorted(await self._redis.smembers(self._settings.active_symbols_key))
        due_map = self._client.initial_due_map()

        for symbol in active_symbols:
            await self._postgres.ensure_content_checkpoint(lane="symbol-report", symbol=symbol, next_due_at=due_map["symbol-report"])
            await self._postgres.ensure_content_checkpoint(lane="symbol-news", symbol=symbol, next_due_at=due_map["symbol-news"])
            await self._postgres.ensure_content_checkpoint(
                lane="symbol-announcement",
                symbol=symbol,
                next_due_at=due_map["symbol-announcement"],
            )

        await self._postgres.ensure_content_checkpoint(lane="market-news", symbol="", next_due_at=due_map["market-news"])
        await self._postgres.delete_inactive_symbol_content_checkpoints(active_symbols)

    async def _run_once(self) -> bool:
        checkpoints = await self._postgres.fetch_content_checkpoints()
        now = datetime.now(UTC)
        due_items = [
            item
            for item in checkpoints
            if isinstance(item.get("next_due_at"), datetime)
            and item["next_due_at"] <= now
            and (item.get("cooldown_until") is None or item["cooldown_until"] <= now)
        ]
        if not due_items:
            return False

        item = due_items[0]
        if item["lane"] != "market-news":
            active_symbols = await self._redis.smembers(self._settings.active_symbols_key)
            if item["symbol"] not in active_symbols:
                await self._postgres.delete_symbol_content_checkpoints(str(item["symbol"]))
                return True
        await self._execute_job(item)
        return True

    async def _execute_job(self, checkpoint: dict[str, object]) -> None:
        lane = str(checkpoint["lane"])
        symbol = str(checkpoint["symbol"])
        started_at = datetime.now(UTC)

        try:
            result = await asyncio.to_thread(self._fetch_lane, lane, symbol)
            await self._persist_result(lane, symbol, result.items)
            finished_at = datetime.now(UTC)
            await self._postgres.upsert_content_checkpoint(
                lane=lane,
                symbol=symbol,
                cursor={},
                next_due_at=finished_at + self._refresh_delta_for_lane(lane),
                cooldown_until=None,
                last_success_at=finished_at,
                last_attempt_at=finished_at,
                failure_count=0,
                last_error=result.warning_message,
            )
            await self._postgres.insert_content_fetch_log(
                lane=lane,
                symbol=symbol or None,
                provider="akshare",
                status="success",
                started_at=started_at,
                finished_at=finished_at,
                http_hint=None,
                error_message=result.warning_message,
                meta={"items": len(result.items), "upstreamSource": result.upstream_source, "warning": result.warning_message},
            )
        except Exception as exc:
            finished_at = datetime.now(UTC)
            failure_count = int(checkpoint.get("failure_count") or 0) + 1
            cooldown_until = finished_at + self._cooldown_delta(failure_count)
            await self._postgres.upsert_content_checkpoint(
                lane=lane,
                symbol=symbol,
                cursor=checkpoint.get("cursor") or {},
                next_due_at=cooldown_until,
                cooldown_until=cooldown_until,
                last_success_at=checkpoint.get("last_success_at"),
                last_attempt_at=finished_at,
                failure_count=failure_count,
                last_error=str(exc),
            )
            await self._postgres.insert_content_fetch_log(
                lane=lane,
                symbol=symbol or None,
                provider="akshare",
                status="failure",
                started_at=started_at,
                finished_at=finished_at,
                http_hint=None,
                error_message=str(exc),
                meta={"failureCount": failure_count},
            )
            logger.exception("content collector job failed", extra={"lane": lane, "symbol": symbol})

    def _fetch_lane(self, lane: str, symbol: str):
        if lane == "symbol-report":
            return self._client.fetch_research_reports(symbol)
        if lane == "symbol-news":
            return self._client.fetch_symbol_news(symbol)
        if lane == "symbol-announcement":
            return self._client.fetch_announcements(symbol)
        if lane == "market-news":
            return self._client.fetch_market_news()
        raise ValueError(f"unsupported content lane: {lane}")

    async def _persist_result(self, lane: str, symbol: str, items: list[dict[str, object]]) -> None:
        if lane == "symbol-report":
            await self._postgres.upsert_research_reports(
                [
                    {
                        "symbol": item["symbol"],
                        "title": item["title"],
                        "rating": item.get("rating"),
                        "institution": item.get("institution"),
                        "analyst": item.get("analyst"),
                        "industry": item.get("industry"),
                        "published_at": item.get("publishedAt"),
                        "first_seen_at": item["firstSeenAt"],
                        "last_seen_at": item["lastSeenAt"],
                        "source_url": item.get("sourceUrl"),
                        "provider": item.get("provider", "akshare"),
                        "upstream_source": item.get("upstreamSource", "eastmoney"),
                        "dedupe_key": item["dedupeKey"],
                        "metrics": item.get("metrics", {}),
                        "raw_payload": item.get("rawPayload", {}),
                    }
                    for item in items
                ]
            )
            return

        if lane in {"symbol-news", "market-news"}:
            await self._postgres.upsert_news_items(
                [
                    {
                        "symbol": item.get("symbol"),
                        "scope": item["scope"],
                        "title": item["title"],
                        "summary": item.get("summary"),
                        "content": item.get("content"),
                        "article_source": item.get("articleSource"),
                        "published_at": item.get("publishedAt"),
                        "first_seen_at": item["firstSeenAt"],
                        "last_seen_at": item["lastSeenAt"],
                        "source_url": item.get("sourceUrl"),
                        "provider": item.get("provider", "akshare"),
                        "upstream_source": item.get("upstreamSource", "eastmoney"),
                        "dedupe_key": item["dedupeKey"],
                        "raw_payload": item.get("rawPayload", {}),
                    }
                    for item in items
                ]
            )
            return

        if lane == "symbol-announcement":
            await self._postgres.upsert_announcement_items(
                [
                    {
                        "symbol": item["symbol"],
                        "title": item["title"],
                        "announcement_type": item.get("announcementType"),
                        "published_at": item.get("publishedAt"),
                        "first_seen_at": item["firstSeenAt"],
                        "last_seen_at": item["lastSeenAt"],
                        "pdf_url": item.get("pdfUrl"),
                        "provider": item.get("provider", "akshare"),
                        "upstream_source": item.get("upstreamSource", "eastmoney"),
                        "dedupe_key": item["dedupeKey"],
                        "raw_payload": item.get("rawPayload", {}),
                    }
                    for item in items
                ]
            )

    def _refresh_delta_for_lane(self, lane: str) -> timedelta:
        if lane == "symbol-report":
            return timedelta(seconds=self._settings.content_report_refresh_seconds)
        if lane == "symbol-news":
            return timedelta(seconds=self._settings.content_news_refresh_seconds)
        if lane == "symbol-announcement":
            return timedelta(seconds=self._settings.content_announcement_refresh_seconds)
        return timedelta(seconds=self._settings.content_market_news_refresh_seconds)

    def _cooldown_delta(self, failure_count: int) -> timedelta:
        base = max(self._settings.content_fetch_cooldown_base_seconds, 60)
        multiplier = min(max(failure_count, 1), 8)
        return timedelta(seconds=base * multiplier)
