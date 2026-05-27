from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from redis.asyncio import Redis

from collector.services.fund_data_client import FundDataClient
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)


class FundCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = FundDataClient(settings)
        self._last_stream_id = "$"
        self._postgres_ready = False
        self._last_refreshed_at: dict[str, datetime] = {}

    async def run(self) -> None:
        if not self._settings.fund_collector_enabled:
            logger.info("fund collector disabled")
            return

        logger.info("fund collector worker started")
        while True:
            try:
                await self._ensure_postgres_connection()
                await self._reconcile_active_funds()
                await self._consume_command_stream()
                await self._refresh_active_funds()
            except Exception:
                self._postgres_ready = False
                logger.exception("fund collector loop failed; retrying")
                await asyncio.sleep(self._settings.fund_collector_poll_interval_seconds)

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect()
        self._postgres_ready = True
        logger.info("fund collector connected to postgres")

    async def _consume_command_stream(self) -> None:
        messages = await self._redis.xread(
            {self._settings.redis_stream_key: self._last_stream_id},
            count=20,
            block=min(self._settings.collector_poll_interval_seconds * 1000, 5000),
        )
        for _, entries in messages:
            await self._handle_entries(entries)

    async def _handle_entries(self, entries: Sequence[tuple[str, dict[str, str]]]) -> None:
        for entry_id, payload in entries:
            self._last_stream_id = entry_id
            event_type = payload.get("event")
            if event_type not in {"activate_fund", "deactivate_fund", "refresh_fund_holdings"}:
                continue

            fund_code = payload.get("fund_code") or payload.get("fundCode")
            if not fund_code:
                continue

            await self._persist_command_event(fund_code, payload)
            if event_type == "activate_fund":
                await self._redis.sadd(self._settings.active_funds_key, fund_code)
                await self._redis.set(
                    f"{self._settings.fund_auto_link_stocks_key_prefix}:{fund_code}",
                    payload.get("auto_link_stocks", "true"),
                )
                await self._refresh_fund(fund_code, auto_link_stocks=self._is_truthy(payload.get("auto_link_stocks", "true")))
            elif event_type == "deactivate_fund":
                await self._redis.srem(self._settings.active_funds_key, fund_code)
                await self._deactivate_fund_links(fund_code)
            elif event_type == "refresh_fund_holdings":
                await self._refresh_fund(fund_code)

    async def _refresh_active_funds(self) -> None:
        active_funds = sorted(await self._redis.smembers(self._settings.active_funds_key))
        logger.info("active funds snapshot", extra={"active_funds": active_funds})
        for fund_code in active_funds:
            last_refreshed = self._last_refreshed_at.get(fund_code)
            if last_refreshed is not None:
                elapsed = (datetime.now(UTC) - last_refreshed).total_seconds()
                if elapsed < self._settings.fund_collector_poll_interval_seconds:
                    continue
            await self._refresh_fund(fund_code)

    async def _refresh_fund(self, fund_code: str, *, auto_link_stocks: bool | None = None) -> None:
        state = await asyncio.to_thread(self._client.fetch_fund_state, fund_code)
        profile = state["profile"]
        snapshot = state["snapshot"]
        nav_history = state["nav_history"]
        holdings = state["holdings"]

        await self._postgres.upsert_fund_profile(profile)
        await self._postgres.upsert_fund_snapshot(snapshot)
        await self._postgres.upsert_fund_nav_rows(nav_history)
        await self._postgres.upsert_fund_holding_rows(holdings)

        linked_symbols = [item["stock_symbol"] for item in holdings if item.get("stock_symbol")]
        auto_linkable_symbols = [symbol for symbol in linked_symbols if self._is_stock_collector_supported_symbol(symbol)]
        previous_symbols = set(await self._redis.smembers(f"{self._settings.active_symbols_key}:fund:{fund_code}"))
        await self._postgres.upsert_fund_stock_links(
            [{"fund_code": fund_code, "stock_symbol": symbol, "link_type": "top-holding"} for symbol in auto_linkable_symbols]
        )
        await self._redis.set(f"{self._settings.fund_snapshot_key_prefix}:{fund_code}", json.dumps(snapshot, default=str))
        await self._redis.set(f"{self._settings.fund_holdings_key_prefix}:{fund_code}:holdings", json.dumps(linked_symbols))

        if auto_link_stocks is None:
            auto_link_payload = await self._redis.get(f"{self._settings.fund_auto_link_stocks_key_prefix}:{fund_code}")
            should_auto_link_stocks = self._is_truthy(auto_link_payload if auto_link_payload is not None else "true")
        else:
            should_auto_link_stocks = auto_link_stocks

        for symbol in linked_symbols:
            if should_auto_link_stocks and self._is_stock_collector_supported_symbol(symbol):
                await self._redis.sadd(self._settings.active_symbols_key, symbol)
                await self._redis.sadd(f"{self._settings.active_symbols_key}:fund:{fund_code}", symbol)
            if self._is_stock_collector_supported_symbol(symbol):
                stock_fund_rows = await asyncio.to_thread(self._client.fetch_stock_fund_holders, symbol)
                await self._postgres.upsert_stock_fund_holding_rows(stock_fund_rows)
                await self._redis.set(
                    f"{self._settings.stock_funds_key_prefix}:{symbol}:funds",
                    json.dumps(sorted({row["fund_code"] for row in stock_fund_rows if row.get("fund_code")})),
                )

        stale_symbols = previous_symbols.difference(linked_symbols)
        for symbol in stale_symbols:
            if not await self._redis.sismember(f"{self._settings.active_symbols_key}:manual", symbol):
                await self._redis.srem(self._settings.active_symbols_key, symbol)
            await self._redis.srem(f"{self._settings.active_symbols_key}:fund:{fund_code}", symbol)
        if stale_symbols:
            await self._postgres.delete_fund_stock_links(fund_code, exclude_stock_symbols=linked_symbols)

        self._last_refreshed_at[fund_code] = datetime.now(UTC)
        logger.info(
            "fund collector refreshed fund state",
            extra={"fund_code": fund_code, "holdings": len(holdings), "nav_rows": len(nav_history)},
        )

    def _is_truthy(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _is_stock_collector_supported_symbol(self, symbol: str | None) -> bool:
        return bool(symbol and symbol.isdigit() and len(symbol) == 6)

    async def _symbol_is_tracked_by_other_active_fund(self, excluding_fund_code: str, symbol: str) -> bool:
        active_funds = await self._redis.smembers(self._settings.active_funds_key)
        for active_fund_code in active_funds:
            if active_fund_code == excluding_fund_code:
                continue
            payload = await self._redis.get(f"{self._settings.fund_holdings_key_prefix}:{active_fund_code}:holdings")
            if not payload:
                continue
            try:
                holdings = json.loads(payload)
            except Exception:
                continue
            if isinstance(holdings, list) and symbol in holdings:
                return True
        return False

    async def _deactivate_fund_links(self, fund_code: str) -> None:
        payload = await self._redis.get(f"{self._settings.fund_holdings_key_prefix}:{fund_code}:holdings")
        linked_symbols = json.loads(payload) if payload else []
        await self._redis.delete(
            f"{self._settings.fund_snapshot_key_prefix}:{fund_code}",
            f"{self._settings.fund_holdings_key_prefix}:{fund_code}:holdings",
        )
        await self._postgres.delete_fund_stock_links(fund_code)
        for symbol in linked_symbols:
            await self._redis.srem(f"{self._settings.active_symbols_key}:fund:{fund_code}", symbol)
            if await self._redis.sismember(f"{self._settings.active_symbols_key}:manual", symbol):
                continue
            if await self._symbol_is_tracked_by_other_active_fund(fund_code, symbol):
                continue
            if await self._postgres.has_other_fund_stock_links(stock_symbol=symbol, excluding_fund_code=fund_code):
                continue
            if await self._redis.sismember(self._settings.active_symbols_key, symbol):
                await self._redis.srem(self._settings.active_symbols_key, symbol)
        self._last_refreshed_at.pop(fund_code, None)

    async def _reconcile_active_funds(self) -> None:
        active_funds = await self._redis.smembers(self._settings.active_funds_key)
        for fund_code in active_funds:
            payload = await self._redis.get(f"{self._settings.fund_holdings_key_prefix}:{fund_code}:holdings")
            if not payload:
                continue
            try:
                linked_symbols = json.loads(payload)
            except Exception:
                continue
            if not isinstance(linked_symbols, list):
                continue
            linked_symbols_set = set(linked_symbols)
            current_symbols = set(await self._redis.smembers(f"{self._settings.active_symbols_key}:fund:{fund_code}"))
            stale_symbols = current_symbols.difference(linked_symbols_set)
            for symbol in stale_symbols:
                if not await self._redis.sismember(f"{self._settings.active_symbols_key}:manual", symbol):
                    await self._redis.srem(self._settings.active_symbols_key, symbol)
                await self._redis.srem(f"{self._settings.active_symbols_key}:fund:{fund_code}", symbol)

    async def _persist_command_event(self, fund_code: str, payload: dict[str, str]) -> None:
        requested_at = payload.get("requested_at")
        timestamp = datetime.fromisoformat(requested_at) if requested_at else datetime.now(UTC)
        await self._postgres.persist_fund_command(
            timestamp=timestamp,
            fund_code=fund_code,
            command_type=payload.get("event", "unknown"),
            payload=payload,
        )
