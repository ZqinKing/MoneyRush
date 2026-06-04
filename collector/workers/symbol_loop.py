from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from time import monotonic
from collections.abc import Sequence

from redis.asyncio import Redis

from collector.services.anomaly_aggregator import AnomalyAggregator
from collector.services.ai_summary_client import is_ai_configured
from collector.services.anomaly_reason_analyzer import AnomalyReasonAnalyzer
from collector.services.persistence import PostgresStore
from collector.services.tencent_quote_client import MarketQuoteClient


logger = logging.getLogger(__name__)
CHINA_MARKET_TZ = timezone(timedelta(hours=8))
INTRADAY_EXPECTED_BUCKET_COUNT = 240
ANOMALY_REASON_INTERVAL_SECONDS = 300
ANOMALY_REASON_BATCH_SIZE = 10


def _derive_llm_audit_status(*, attempted: bool, llm_succeeded: bool) -> str:
    if not attempted:
        return "skipped"
    return "completed" if llm_succeeded else "failed"


class CollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._anomaly_aggregator = AnomalyAggregator(
            self._postgres,
            ai_reason_enabled=is_ai_configured(settings),
        )
        self._anomaly_reason_analyzer = AnomalyReasonAnalyzer(settings)
        self._quote_client = MarketQuoteClient(settings)
        self._last_stream_id = "$"
        self._last_collected_at: dict[str, float] = {}
        self._last_market_state_identity: dict[str, tuple[object, ...]] = {}
        self._symbol_poll_interval_seconds: dict[str, float] = {}
        self._unchanged_quote_counts: dict[str, int] = {}
        self._daily_history_synced_for_trade_day: dict[str, str] = {}
        self._intraday_history_terminal_for_trade_day: dict[str, tuple[str, str]] = {}
        self._intraday_history_last_refresh_at: dict[str, float] = {}
        self._latest_daily_trade_day_by_symbol: dict[str, date] = {}
        self._last_anomaly_aggregation_at = 0.0
        self._last_anomaly_reason_analysis_at = 0.0
        self._postgres_ready = False

    async def run(self) -> None:
        logger.info("collector worker started")

        while True:
            try:
                await self._ensure_postgres_connection()
                await self._consume_command_stream()
                await self._aggregate_active_symbol_anomalies()
                await self._analyze_pending_anomaly_reasons()
                await self._collect_active_symbols()
                await self._aggregate_active_symbol_anomalies(force=True)
                await self._analyze_pending_anomaly_reasons(force=True)
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
            await self._safe_ensure_daily_history(symbol)
            await self._safe_ensure_intraday_history(symbol)
            await self._collect_symbol(symbol)

    async def _aggregate_active_symbol_anomalies(self, *, force: bool = False) -> None:
        if not self._settings.anomaly_aggregation_enabled:
            return
        now = monotonic()
        interval_seconds = max(float(self._settings.anomaly_aggregation_interval_seconds), 60.0)
        if not force and now - self._last_anomaly_aggregation_at < interval_seconds:
            return
        self._last_anomaly_aggregation_at = now
        active_symbols = sorted(await self._redis.smembers(self._settings.active_symbols_key))
        try:
            anomaly_count = await self._anomaly_aggregator.aggregate_daily_anomalies(active_symbols)
        except Exception:
            logger.exception("collector anomaly aggregation failed")
            return
        logger.info("collector aggregated significant anomalies", extra={"active_symbol_count": len(active_symbols), "anomaly_count": anomaly_count})

    async def _analyze_pending_anomaly_reasons(self, *, force: bool = False) -> None:
        if not is_ai_configured(self._settings):
            return
        now = monotonic()
        interval_seconds = ANOMALY_REASON_INTERVAL_SECONDS
        if not force and now - self._last_anomaly_reason_analysis_at < interval_seconds:
            return
        self._last_anomaly_reason_analysis_at = now
        batch_limit = ANOMALY_REASON_BATCH_SIZE
        analyzed_count = 0
        try:
            rows = await self._postgres.fetch_pending_anomaly_reasons(limit=batch_limit)
            if not rows:
                logger.info("collector analyzed anomaly reasons", extra={"anomaly_count": 0})
                return

            updates = []
            audit_rows = []
            for row in rows:
                since_ts, until_ts = self._anomaly_reason_analyzer.reason_window(row["first_trigger_ts"])
                context = await self._postgres.fetch_anomaly_reason_context(
                    symbol=str(row["symbol"]),
                    since_ts=since_ts,
                    until_ts=until_ts,
                    trigger_ts=row["first_trigger_ts"],
                    anomaly_date=row["anomaly_date"],
                    limit_per_kind=8,
                )
                result = await asyncio.to_thread(self._anomaly_reason_analyzer.analyze, row, context)
                invoked_at = datetime.now(UTC)
                updates.append(
                    {
                        "id": row["id"],
                        "ai_reason": result.reason,
                        "ai_reason_status": result.status,
                        "ai_reason_generated_at": datetime.now(UTC) if result.status == "completed" else None,
                        "related_news_ids": result.related_news_ids,
                        "related_announcement_ids": result.related_announcement_ids,
                    }
                )
                audit_rows.append(
                    {
                        "invoked_at": invoked_at,
                        "audit_date": invoked_at.astimezone(CHINA_MARKET_TZ).date(),
                        "menu_module": "events",
                        "call_category": "anomaly_reason",
                        "status": _derive_llm_audit_status(attempted=result.attempted, llm_succeeded=result.llm_succeeded),
                        "model_used": result.model_used,
                        "prompt_version": result.prompt_version,
                        "latency_ms": result.latency_ms,
                        "meta": {
                            "anomalyId": row["id"],
                            "symbol": str(row["symbol"] or ""),
                            "anomalyType": str(row["anomaly_type"] or ""),
                            "llmSucceeded": result.llm_succeeded,
                            "skipReason": result.skip_reason,
                            "relatedNewsCount": len(result.related_news_ids),
                            "relatedAnnouncementCount": len(result.related_announcement_ids),
                        },
                    }
                )

            if updates:
                await self._postgres.update_anomaly_ai_reasons(updates)
            if audit_rows:
                await self._postgres.insert_llm_audit_rows(audit_rows)
            analyzed_count = len(rows)
        except Exception:
            logger.exception("collector anomaly reason analysis failed")
            return
        logger.info("collector analyzed anomaly reasons", extra={"anomaly_count": analyzed_count})

    async def _collect_symbol(self, symbol: str) -> None:
        if not await self._redis.sismember(self._settings.active_symbols_key, symbol):
            self._clear_symbol_runtime_state(symbol)
            return

        now = monotonic()
        last_collected_at = self._last_collected_at.get(symbol)
        min_interval_seconds = self._symbol_poll_interval_seconds.get(
            symbol,
            float(self._settings.collector_symbol_min_interval_seconds),
        )
        if last_collected_at is not None and now - last_collected_at < min_interval_seconds:
            return

        self._last_collected_at[symbol] = now
        market_state = await asyncio.to_thread(self._quote_client.fetch_quote, symbol)
        market_state_identity = self._market_state_identity(market_state)
        previous_identity = self._last_market_state_identity.get(symbol)

        if market_state_identity == previous_identity:
            unchanged_quote_count = self._unchanged_quote_counts.get(symbol, 0) + 1
            self._unchanged_quote_counts[symbol] = unchanged_quote_count
            self._symbol_poll_interval_seconds[symbol] = self._next_symbol_poll_interval_seconds(unchanged_quote_count)
            logger.info(
                "collector skipped unchanged market state",
                extra={
                    "symbol": symbol,
                    "unchanged_quote_count": unchanged_quote_count,
                    "next_poll_interval_seconds": self._symbol_poll_interval_seconds[symbol],
                    "updated_at": market_state["snapshot"].get("updatedAt"),
                    "source": market_state["snapshot"].get("source"),
                },
            )
            return

        self._last_market_state_identity[symbol] = market_state_identity
        self._unchanged_quote_counts[symbol] = 0
        self._symbol_poll_interval_seconds[symbol] = float(self._settings.collector_symbol_min_interval_seconds)
        await self._postgres.persist_market_state(
            snapshot=market_state["snapshot"],
            tick=market_state["tick"],
            kline=market_state["kline"],
            event=market_state["event"],
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
                "source": market_state["snapshot"]["source"],
            },
        )

    def _clear_symbol_runtime_state(self, symbol: str) -> None:
        self._last_collected_at.pop(symbol, None)
        self._last_market_state_identity.pop(symbol, None)
        self._symbol_poll_interval_seconds.pop(symbol, None)
        self._unchanged_quote_counts.pop(symbol, None)
        self._daily_history_synced_for_trade_day.pop(symbol, None)
        self._intraday_history_terminal_for_trade_day.pop(symbol, None)
        self._intraday_history_last_refresh_at.pop(symbol, None)
        self._latest_daily_trade_day_by_symbol.pop(symbol, None)

    def _market_state_identity(self, market_state: dict[str, dict[str, object]]) -> tuple[object, ...]:
        snapshot = market_state.get("snapshot") or {}
        tick = market_state.get("tick") or {}
        kline = market_state.get("kline") or {}
        event = market_state.get("event") or {}
        return (
            snapshot.get("updatedAt"),
            snapshot.get("lastPrice"),
            snapshot.get("changePct"),
            snapshot.get("source"),
            tick.get("volume"),
            tick.get("amount"),
            tick.get("side"),
            kline.get("open"),
            kline.get("high"),
            kline.get("low"),
            kline.get("close"),
            kline.get("volume"),
            kline.get("amount"),
            event.get("type"),
        )

    def _next_symbol_poll_interval_seconds(self, unchanged_quote_count: int) -> float:
        threshold = max(int(self._settings.collector_unchanged_quote_backoff_threshold), 1)
        if unchanged_quote_count < threshold:
            return float(self._settings.collector_symbol_min_interval_seconds)

        base_seconds = max(int(self._settings.collector_unchanged_quote_backoff_base_seconds), self._settings.collector_symbol_min_interval_seconds)
        max_seconds = max(int(self._settings.collector_unchanged_quote_backoff_max_seconds), base_seconds)
        exponent = unchanged_quote_count - threshold
        return float(min(base_seconds * (2 ** exponent), max_seconds))

    async def _safe_ensure_daily_history(self, symbol: str) -> None:
        try:
            await self._ensure_daily_history(symbol)
        except Exception:
            logger.exception("collector daily history backfill failed; continuing symbol sweep", extra={"symbol": symbol})

    async def _safe_ensure_intraday_history(self, symbol: str) -> None:
        try:
            await self._ensure_intraday_history(symbol)
        except Exception:
            logger.exception("collector intraday history backfill failed; continuing symbol sweep", extra={"symbol": symbol})

    async def _ensure_daily_history(self, symbol: str) -> None:
        trade_day = datetime.now(CHINA_MARKET_TZ).date().isoformat()
        if self._daily_history_synced_for_trade_day.get(symbol) == trade_day:
            return

        history = await asyncio.to_thread(self._quote_client.fetch_daily_history, symbol, 60)
        if history:
            await self._postgres.persist_kline_history(history)
            latest_bucket = history[0].get("bucketTs")
            if isinstance(latest_bucket, datetime):
                self._latest_daily_trade_day_by_symbol[symbol] = latest_bucket.astimezone(CHINA_MARKET_TZ).date()
            logger.info(
                "collector backfilled daily kline history",
                extra={
                    "symbol": symbol,
                    "trade_day": trade_day,
                    "rows": len(history),
                },
            )

        self._daily_history_synced_for_trade_day[symbol] = trade_day

    async def _ensure_intraday_history(self, symbol: str) -> None:
        if not self._settings.collector_intraday_history_enabled:
            return

        trade_day_date = self._latest_daily_trade_day_by_symbol.get(symbol)
        if trade_day_date is None:
            daily_history = await asyncio.to_thread(self._quote_client.fetch_daily_history, symbol, 1)
            if not daily_history:
                return
            latest_daily_bucket = daily_history[0].get("bucketTs")
            if not isinstance(latest_daily_bucket, datetime):
                return
            trade_day_date = latest_daily_bucket.astimezone(CHINA_MARKET_TZ).date()
            self._latest_daily_trade_day_by_symbol[symbol] = trade_day_date

        if trade_day_date is None:
            return

        trade_day = trade_day_date.isoformat()
        terminal_state = self._intraday_history_terminal_for_trade_day.get(symbol)
        if terminal_state is not None and terminal_state[0] == trade_day:
            return

        if self._should_skip_intraday_refresh(symbol, trade_day_date):
            return

        history = await asyncio.to_thread(self._quote_client.fetch_intraday_history, symbol, trade_day_date)
        self._intraday_history_last_refresh_at[symbol] = monotonic()
        terminal_status = self._resolve_intraday_terminal_status(trade_day_date, history)
        if history:
            await self._postgres.persist_kline_history(history)
            logger.info(
                "collector backfilled intraday kline history",
                extra={
                    "symbol": symbol,
                    "trade_day": trade_day,
                    "rows": len(history),
                    "terminal_status": terminal_status,
                },
            )
        else:
            logger.warning(
                "collector intraday kline backfill returned no rows",
                extra={
                    "symbol": symbol,
                    "trade_day": trade_day,
                    "terminal_status": terminal_status,
                },
            )

        if terminal_status is not None:
            self._intraday_history_terminal_for_trade_day[symbol] = (trade_day, terminal_status)

    def _should_skip_intraday_refresh(self, symbol: str, trade_day_date: date) -> bool:
        if trade_day_date != datetime.now(CHINA_MARKET_TZ).date():
            return False

        last_refresh_at = self._intraday_history_last_refresh_at.get(symbol)
        if last_refresh_at is None:
            return False

        refresh_seconds = self._intraday_refresh_interval_seconds(trade_day_date)
        return monotonic() - last_refresh_at < refresh_seconds

    def _intraday_refresh_interval_seconds(self, trade_day_date: date) -> int:
        base_refresh_seconds = max(int(self._settings.collector_intraday_history_refresh_seconds), 1)
        if self._is_within_intraday_reconciliation_window(trade_day_date):
            reconciliation_seconds = max(int(self._settings.collector_intraday_post_close_reconciliation_seconds), 0)
            if reconciliation_seconds > 0:
                return min(base_refresh_seconds, reconciliation_seconds)
        return base_refresh_seconds

    def _resolve_intraday_terminal_status(self, trade_day_date: date, history: list[dict[str, object]]) -> str | None:
        if trade_day_date != datetime.now(CHINA_MARKET_TZ).date():
            return "complete" if self._is_intraday_history_complete(trade_day_date, history) else "incomplete"

        if self._is_intraday_history_complete(trade_day_date, history):
            return "complete"

        if not self._is_within_intraday_reconciliation_window(trade_day_date):
            return "incomplete"

        return None

    def _is_intraday_history_complete(self, trade_day_date: date, history: list[dict[str, object]]) -> bool:
        bucket_ts_values = {
            item.get("bucketTs")
            for item in history
            if isinstance(item.get("bucketTs"), datetime)
        }
        expected_final_bucket = self._expected_intraday_final_bucket(trade_day_date)
        return len(bucket_ts_values) >= INTRADAY_EXPECTED_BUCKET_COUNT and expected_final_bucket in bucket_ts_values

    def _is_within_intraday_reconciliation_window(self, trade_day_date: date) -> bool:
        now_local = datetime.now(CHINA_MARKET_TZ)
        if now_local.date() != trade_day_date:
            return False

        market_close_local = datetime(trade_day_date.year, trade_day_date.month, trade_day_date.day, 15, 0, tzinfo=CHINA_MARKET_TZ)
        reconciliation_end_local = market_close_local + timedelta(
            seconds=max(int(self._settings.collector_intraday_post_close_reconciliation_seconds), 0)
        )
        return now_local <= reconciliation_end_local

    @staticmethod
    def _expected_intraday_final_bucket(trade_day_date: date) -> datetime:
        return datetime(
            trade_day_date.year,
            trade_day_date.month,
            trade_day_date.day,
            14,
            59,
            tzinfo=CHINA_MARKET_TZ,
        ).astimezone(UTC)

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
                    await self._persist_command_event(symbol, payload)
                    await self._redis.sadd(self._settings.active_symbols_key, symbol)
                    return

            if payload.get("event") == "deactivate_symbol":
                symbol = payload.get("symbol")
                if symbol:
                    await self._persist_command_event(symbol, payload)
                    await self._redis.srem(self._settings.active_symbols_key, symbol)
                    await self._redis.delete(
                        f"{self._settings.market_snapshot_key_prefix}:{symbol}",
                        f"{self._settings.market_event_key_prefix}:{symbol}",
                    )
                    self._clear_symbol_runtime_state(symbol)

    async def _persist_command_event(self, symbol: str, payload: dict[str, str]) -> None:
        requested_at = payload.get("requested_at")
        if requested_at:
            timestamp = datetime.fromisoformat(requested_at)
        else:
            timestamp = datetime.now(UTC)

        await self._postgres.persist_symbol_command(
            timestamp=timestamp,
            symbol=symbol,
            command_type=payload.get("event", "unknown"),
            payload=payload,
        )
