from __future__ import annotations

import asyncio
import logging
import re
from html import unescape
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from collector.services.ai_summary_client import AiSummaryClient
from collector.services.akshare_content_client import AkshareContentClient
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")


def _sanitize_news_text(value: object) -> str | None:
    if value is None:
        return None
    text = _TAG_RE.sub("", unescape(str(value))).replace("\xa0", " ").strip()
    return text or None


def _coerce_utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ContentCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = AkshareContentClient(settings)
        self._ai_summary_client = AiSummaryClient(settings)
        self._postgres_ready = False
        self._ai_summary_backfill_tasks: set[asyncio.Task[None]] = set()
        self._ai_summary_backfill_semaphore = asyncio.Semaphore(1)

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

        active_symbols: set[str] | None = None
        batch_size = max(int(self._settings.content_collector_batch_size), 1)
        processed_any = False
        for item in due_items[:batch_size]:
            if item["lane"] != "market-news":
                if active_symbols is None:
                    active_symbols = set(await self._redis.smembers(self._settings.active_symbols_key))
                if item["symbol"] not in active_symbols:
                    await self._postgres.delete_symbol_content_checkpoints(str(item["symbol"]))
                    processed_any = True
                    continue
            await self._execute_job(item)
            processed_any = True
        if processed_any and len(due_items) > batch_size:
            logger.info(
                "content collector batch processed due lanes",
                extra={
                    "batchSize": batch_size,
                    "processed": min(len(due_items), batch_size),
                    "remainingDue": len(due_items) - batch_size,
                },
            )
        if processed_any:
            return True
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
            news_rows = [
                {
                    "symbol": item.get("symbol"),
                    "scope": item["scope"],
                    "title": _sanitize_news_text(item["title"]) or item["title"],
                    "summary": _sanitize_news_text(item.get("summary")),
                    "content": _sanitize_news_text(item.get("content")),
                    "article_source": item.get("articleSource"),
                    "published_at": item.get("publishedAt"),
                    "first_seen_at": item["firstSeenAt"],
                    "last_seen_at": item["lastSeenAt"],
                    "source_url": item.get("sourceUrl"),
                    "ai_summary": None,
                    "provider": item.get("provider", "akshare"),
                    "upstream_source": item.get("upstreamSource", "eastmoney"),
                    "dedupe_key": item["dedupeKey"],
                    "raw_payload": item.get("rawPayload", {}),
                }
                for item in items
            ]
            await self._postgres.upsert_news_items(news_rows)
            if self._settings.content_ai_summary_enabled and lane == "symbol-news":
                await self._schedule_ai_summary_backfill(news_rows)
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

    async def _schedule_ai_summary_backfill(self, items: list[dict[str, object]]) -> None:
        existing_state = await self._postgres.fetch_news_ai_summary_state(
            [str(item.get("dedupe_key") or "") for item in items if str(item.get("dedupe_key") or "").strip()]
        )
        eligible_items = [
            item
            for item in items
            if str(item.get("title") or "").strip()
            and str(item.get("content") or "").strip()
            and str(item.get("dedupe_key") or "").strip()
            and not str(existing_state.get(str(item.get("dedupe_key") or "")) or "").strip()
            and self._is_ai_summary_candidate_fresh(item)
        ]
        if not eligible_items:
            return

        task = asyncio.create_task(self._run_ai_summary_backfill(eligible_items))
        self._ai_summary_backfill_tasks.add(task)
        task.add_done_callback(self._on_ai_summary_backfill_done)

    def _is_ai_summary_candidate_fresh(self, item: dict[str, object]) -> bool:
        published_at = _coerce_utc_datetime(item.get("published_at"))
        first_seen_at = _coerce_utc_datetime(item.get("first_seen_at"))
        if published_at is None or first_seen_at is None:
            return False
        max_age_seconds = max(int(self._settings.content_ai_summary_max_news_age_seconds), 0)
        age_seconds = (first_seen_at - published_at).total_seconds()
        return age_seconds <= max_age_seconds

    async def _run_ai_summary_backfill(self, items: list[dict[str, object]]) -> None:
        async with self._ai_summary_backfill_semaphore:
            generated = await asyncio.to_thread(self._generate_ai_summaries, items)
            if generated:
                await self._postgres.update_news_ai_summaries(generated)

    def _generate_ai_summaries(self, items: list[dict[str, object]]) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        for item in items:
            result = self._ai_summary_client.summarize(
                title=str(item.get("title") or "").strip(),
                article_source=str(item.get("article_source") or "").strip() or None,
                raw_summary=str(item.get("summary") or "").strip() or None,
                content=str(item.get("content") or "").strip(),
            )
            if result.summary:
                summaries.append(
                    {
                        "dedupe_key": str(item["dedupe_key"]),
                        "ai_summary": result.summary,
                    }
                )
                continue
            if result.skip_reason:
                logger.info(
                    "content ai summary skipped",
                    extra={
                        "dedupeKey": str(item.get("dedupe_key") or ""),
                        "symbol": str(item.get("symbol") or "") or None,
                        "skipReason": result.skip_reason,
                    },
                )
        return summaries

    def _on_ai_summary_backfill_done(self, task: asyncio.Task[None]) -> None:
        self._ai_summary_backfill_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception("content ai summary backfill task failed", exc_info=exc)

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
