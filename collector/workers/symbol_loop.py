from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from time import monotonic
from collections.abc import Sequence

from redis.asyncio import Redis

from collector.services.persistence import PostgresStore
from collector.services.tencent_quote_client import MarketQuoteClient


logger = logging.getLogger(__name__)
CHINA_MARKET_TZ = timezone(timedelta(hours=8))


class CollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._quote_client = MarketQuoteClient(settings)
        self._last_stream_id = "$"
        self._last_collected_at: dict[str, float] = {}
        self._daily_history_synced_for_trade_day: dict[str, str] = {}
        self._intraday_history_synced_for_trade_day: dict[str, str] = {}
        self._latest_daily_trade_day_by_symbol: dict[str, date] = {}
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
            await self._ensure_daily_history(symbol)
            await self._ensure_intraday_history(symbol)
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
        market_state = await asyncio.to_thread(self._quote_client.fetch_quote, symbol)
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
        self._daily_history_synced_for_trade_day.pop(symbol, None)
        self._intraday_history_synced_for_trade_day.pop(symbol, None)
        self._latest_daily_trade_day_by_symbol.pop(symbol, None)

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
        if self._intraday_history_synced_for_trade_day.get(symbol) == trade_day:
            return

        history = await asyncio.to_thread(self._quote_client.fetch_intraday_history, symbol, trade_day_date)
        if history:
            await self._postgres.persist_kline_history(history)
            self._intraday_history_synced_for_trade_day[symbol] = trade_day
            logger.info(
                "collector backfilled intraday kline history",
                extra={
                    "symbol": symbol,
                    "trade_day": trade_day,
                    "rows": len(history),
                },
            )
        else:
            logger.warning(
                "collector intraday kline backfill returned no rows",
                extra={
                    "symbol": symbol,
                    "trade_day": trade_day,
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
