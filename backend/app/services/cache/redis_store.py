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
        market_overview_cache_key: str = "moneyrush:market:overview",
        active_funds_key: str = "moneyrush:active_funds",
        fund_snapshot_key_prefix: str = "moneyrush:fund:snapshot",
        fund_holdings_key_prefix: str = "moneyrush:fund",
        fund_auto_link_stocks_key_prefix: str = "moneyrush:fund:auto_link",
        stock_funds_key_prefix: str = "moneyrush:stock",
        content_feed_cache_key_prefix: str = "moneyrush:content:feed",
        content_status_cache_key_prefix: str = "moneyrush:content:status",
        dragon_tiger_cache_key_prefix: str = "moneyrush:dragon_tiger",
        macro_snapshot_cache_key: str = "moneyrush:macro:snapshot",
        macro_analysis_latest_cache_key: str = "moneyrush:macro:analysis:latest",
        macro_collector_status_cache_key: str = "moneyrush:macro:collector_status",
    ) -> None:
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._stream_key = stream_key
        self._active_symbols_key = active_symbols_key
        self._market_snapshot_key_prefix = market_snapshot_key_prefix
        self._market_event_key_prefix = market_event_key_prefix
        self._market_events_stream_key = market_events_stream_key
        self._market_overview_cache_key = market_overview_cache_key
        self._active_funds_key = active_funds_key
        self._fund_snapshot_key_prefix = fund_snapshot_key_prefix
        self._fund_holdings_key_prefix = fund_holdings_key_prefix
        self._fund_auto_link_stocks_key_prefix = fund_auto_link_stocks_key_prefix
        self._stock_funds_key_prefix = stock_funds_key_prefix
        self._content_feed_cache_key_prefix = content_feed_cache_key_prefix
        self._content_status_cache_key_prefix = content_status_cache_key_prefix
        self._dragon_tiger_cache_key_prefix = dragon_tiger_cache_key_prefix
        self._macro_snapshot_cache_key = macro_snapshot_cache_key
        self._macro_analysis_latest_cache_key = macro_analysis_latest_cache_key
        self._macro_collector_status_cache_key = macro_collector_status_cache_key

    def _snapshot_key(self, symbol: str) -> str:
        return f"{self._market_snapshot_key_prefix}:{symbol}"

    def _event_key(self, symbol: str) -> str:
        return f"{self._market_event_key_prefix}:{symbol}"

    def _content_feed_key(self, cache_key: str) -> str:
        return f"{self._content_feed_cache_key_prefix}:{cache_key}"

    def _content_status_key(self, cache_key: str) -> str:
        return f"{self._content_status_cache_key_prefix}:{cache_key}"

    def _dragon_tiger_key(self, cache_key: str) -> str:
        return f"{self._dragon_tiger_cache_key_prefix}:{cache_key}"

    def _dragon_tiger_stale_key(self, cache_key: str) -> str:
        return f"{self._dragon_tiger_cache_key_prefix}:stale:{cache_key}"

    def _fund_snapshot_key(self, fund_code: str) -> str:
        return f"{self._fund_snapshot_key_prefix}:{fund_code}"

    def _fund_holdings_key(self, fund_code: str) -> str:
        return f"{self._fund_holdings_key_prefix}:{fund_code}:holdings"

    def _fund_auto_link_stocks_key(self, fund_code: str) -> str:
        return f"{self._fund_auto_link_stocks_key_prefix}:{fund_code}"

    def _stock_funds_key(self, symbol: str) -> str:
        return f"{self._stock_funds_key_prefix}:{symbol}:funds"

    def _manual_active_symbols_key(self) -> str:
        return f"{self._active_symbols_key}:manual"

    async def _delete_by_pattern(self, pattern: str) -> None:
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await self._redis.delete(*keys)
            if cursor == 0:
                break

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def activate_symbol(self, symbol: str) -> str:
        timestamp = datetime.now(UTC).isoformat()
        await self._redis.sadd(self._manual_active_symbols_key(), symbol)
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
        await self._redis.srem(self._manual_active_symbols_key(), symbol)
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

    async def is_symbol_active(self, symbol: str) -> bool:
        return bool(await self._redis.sismember(self._active_symbols_key, symbol))

    async def is_symbol_manually_active(self, symbol: str) -> bool:
        return bool(await self._redis.sismember(self._manual_active_symbols_key(), symbol))

    async def activate_symbol_auto(self, symbol: str) -> None:
        await self._redis.sadd(self._active_symbols_key, symbol)

    async def deactivate_symbol_auto(self, symbol: str) -> None:
        if not await self.is_symbol_manually_active(symbol):
            await self._redis.srem(self._active_symbols_key, symbol)

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

    async def get_market_overview(self) -> dict[str, object] | None:
        payload = await self._redis.get(self._market_overview_cache_key)
        if payload is None:
            return None
        return json.loads(payload)

    async def set_market_overview(self, payload: dict[str, object]) -> None:
        await self._redis.set(self._market_overview_cache_key, json.dumps(payload))

    async def clear_content_caches(self) -> None:
        await self._delete_by_pattern(f"{self._content_feed_cache_key_prefix}:*")
        await self._delete_by_pattern(f"{self._content_status_cache_key_prefix}:*")

    async def get_active_funds(self) -> list[str]:
        funds = await self._redis.smembers(self._active_funds_key)
        return sorted(funds)

    async def is_fund_active(self, fund_code: str) -> bool:
        return bool(await self._redis.sismember(self._active_funds_key, fund_code))

    async def activate_fund(self, fund_code: str, auto_link_stocks: bool = True) -> str:
        timestamp = datetime.now(UTC).isoformat()
        await self._redis.sadd(self._active_funds_key, fund_code)
        await self._redis.set(self._fund_auto_link_stocks_key(fund_code), json.dumps(auto_link_stocks))
        return await self._redis.xadd(
            self._stream_key,
            {
                "event": "activate_fund",
                "fund_code": fund_code,
                "auto_link_stocks": "true" if auto_link_stocks else "false",
                "requested_at": timestamp,
            },
        )

    async def deactivate_fund(self, fund_code: str) -> str:
        timestamp = datetime.now(UTC).isoformat()
        await self._redis.srem(self._active_funds_key, fund_code)
        await self._redis.delete(self._fund_snapshot_key(fund_code), self._fund_auto_link_stocks_key(fund_code))
        return await self._redis.xadd(
            self._stream_key,
            {
                "event": "deactivate_fund",
                "fund_code": fund_code,
                "requested_at": timestamp,
            },
        )

    async def set_fund_snapshot(self, fund_code: str, payload: dict[str, object]) -> None:
        await self._redis.set(self._fund_snapshot_key(fund_code), json.dumps(payload))

    async def get_fund_snapshot(self, fund_code: str) -> dict[str, object] | None:
        payload = await self._redis.get(self._fund_snapshot_key(fund_code))
        if payload is None:
            return None
        return json.loads(payload)

    async def get_fund_snapshots(self, fund_codes: list[str]) -> dict[str, dict[str, object]]:
        if not fund_codes:
            return {}

        values = await self._redis.mget([self._fund_snapshot_key(fund_code) for fund_code in fund_codes])
        snapshots: dict[str, dict[str, object]] = {}
        for fund_code, value in zip(fund_codes, values, strict=False):
            if value is not None:
                snapshots[fund_code] = json.loads(value)
        return snapshots

    async def set_fund_holdings(self, fund_code: str, stock_symbols: list[str]) -> None:
        await self._redis.set(self._fund_holdings_key(fund_code), json.dumps(sorted(set(stock_symbols))))

    async def get_fund_holdings(self, fund_code: str) -> list[str]:
        payload = await self._redis.get(self._fund_holdings_key(fund_code))
        if payload is None:
            return []
        value = json.loads(payload)
        return value if isinstance(value, list) else []

    async def set_stock_funds(self, symbol: str, fund_codes: list[str]) -> None:
        await self._redis.set(self._stock_funds_key(symbol), json.dumps(sorted(set(fund_codes))))

    async def get_stock_funds(self, symbol: str) -> list[str]:
        payload = await self._redis.get(self._stock_funds_key(symbol))
        if payload is None:
            return []
        value = json.loads(payload)
        return value if isinstance(value, list) else []

    async def get_content_feed_cache(self, cache_key: str) -> dict[str, object] | None:
        payload = await self._redis.get(self._content_feed_key(cache_key))
        if payload is None:
            return None
        return json.loads(payload)

    async def set_content_feed_cache(self, cache_key: str, payload: dict[str, object], ttl_seconds: int) -> None:
        await self._redis.set(self._content_feed_key(cache_key), json.dumps(payload), ex=ttl_seconds)

    async def get_content_status_cache(self, cache_key: str) -> dict[str, object] | None:
        payload = await self._redis.get(self._content_status_key(cache_key))
        if payload is None:
            return None
        return json.loads(payload)

    async def set_content_status_cache(self, cache_key: str, payload: dict[str, object], ttl_seconds: int) -> None:
        await self._redis.set(self._content_status_key(cache_key), json.dumps(payload), ex=ttl_seconds)

    async def get_dragon_tiger_cache(self, cache_key: str) -> dict[str, object] | None:
        payload = await self._redis.get(self._dragon_tiger_key(cache_key))
        if payload is None:
            return None
        return json.loads(payload)

    async def set_dragon_tiger_cache(self, cache_key: str, payload: dict[str, object], ttl_seconds: int) -> None:
        await self._redis.set(self._dragon_tiger_key(cache_key), json.dumps(payload), ex=ttl_seconds)

    async def get_dragon_tiger_stale_cache(self, cache_key: str) -> dict[str, object] | None:
        payload = await self._redis.get(self._dragon_tiger_stale_key(cache_key))
        if payload is None:
            return None
        return json.loads(payload)

    async def set_dragon_tiger_stale_cache(self, cache_key: str, payload: dict[str, object], ttl_seconds: int) -> None:
        await self._redis.set(self._dragon_tiger_stale_key(cache_key), json.dumps(payload), ex=ttl_seconds)

    async def get_macro_snapshot(self) -> dict[str, object] | None:
        payload = await self._redis.get(self._macro_snapshot_cache_key)
        if payload is None:
            return None
        return json.loads(payload)

    async def set_macro_snapshot(self, payload: dict[str, object]) -> None:
        await self._redis.set(self._macro_snapshot_cache_key, json.dumps(payload))

    async def get_macro_analysis_latest(self) -> dict[str, object] | None:
        payload = await self._redis.get(self._macro_analysis_latest_cache_key)
        if payload is None:
            return None
        return json.loads(payload)

    async def set_macro_analysis_latest(self, payload: dict[str, object]) -> None:
        await self._redis.set(self._macro_analysis_latest_cache_key, json.dumps(payload))

    async def get_macro_collector_status(self) -> dict[str, object] | None:
        payload = await self._redis.get(self._macro_collector_status_cache_key)
        if payload is None:
            return None
        return json.loads(payload)

    async def set_macro_collector_status(self, payload: dict[str, object]) -> None:
        await self._redis.set(self._macro_collector_status_cache_key, json.dumps(payload))
