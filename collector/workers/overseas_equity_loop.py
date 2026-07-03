from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from time import monotonic
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from collector.services.global_equity_quote_client import GlobalEquityQuoteClient
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)
US_MARKET_TZ = ZoneInfo("America/New_York")
HK_MARKET_TZ = ZoneInfo("Asia/Hong_Kong")
OVERSEAS_REALTIME_SOURCE = "overseas-realtime-aggregated"


class OverseasEquityCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = GlobalEquityQuoteClient(settings)
        self._postgres_ready = False
        self._last_collected_at: dict[str, float] = {}

    async def run(self, *, run_once: bool = False) -> None:
        if not self._settings.overseas_equity_collector_enabled:
            logger.info("overseas equity collector disabled")
            return

        logger.info("overseas equity collector started")
        while True:
            try:
                await self._ensure_postgres_connection()
                await self.refresh_once()
            except Exception:
                self._postgres_ready = False
                logger.exception("overseas equity collector loop failed; retrying")
            if run_once:
                return
            await asyncio.sleep(self._settings.overseas_equity_refresh_seconds)

    async def refresh_once(self) -> list[str]:
        symbols = sorted(await self._redis.smembers(self._settings.active_overseas_symbols_key))
        logger.info("active overseas symbols snapshot", extra={"active_overseas_symbols": symbols})
        collected_symbols: list[str] = []
        for symbol in symbols:
            try:
                if await self._collect_symbol(symbol):
                    collected_symbols.append(symbol)
            except Exception:
                logger.exception("overseas equity collector skipped symbol after quote fetch failed", extra={"symbol": symbol})
        return collected_symbols

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect()
        self._postgres_ready = True
        logger.info("overseas equity collector connected to postgres")

    async def _collect_symbol(self, symbol: str) -> bool:
        if not await self._redis.sismember(self._settings.active_overseas_symbols_key, symbol):
            self._last_collected_at.pop(symbol, None)
            return False

        now = monotonic()
        previous = self._last_collected_at.get(symbol)
        market = _infer_overseas_market(symbol)
        refresh_seconds = self._symbol_refresh_seconds(market)
        if previous is not None and now - previous < refresh_seconds:
            return False
        self._last_collected_at[symbol] = now

        market_state = await asyncio.to_thread(self._client.fetch_quote, symbol)
        await self._postgres.persist_market_state(
            snapshot=market_state["snapshot"],
            tick=market_state["tick"],
            kline=market_state["kline"],
            event=market_state["event"],
        )
        realtime_intraday_bar = self._build_realtime_intraday_bar(market_state)
        if realtime_intraday_bar is not None:
            await self._postgres.persist_kline_history([realtime_intraday_bar])
        await self._redis.set(
            f"{self._settings.market_snapshot_key_prefix}:{symbol}",
            json.dumps(market_state["snapshot"]),
        )
        await self._redis.set(
            f"{self._settings.market_event_key_prefix}:{symbol}",
            json.dumps(market_state["event"], default=str),
        )
        logger.info(
            "overseas equity collector persisted market state",
            extra={
                "symbol": symbol,
                "company_name": market_state["snapshot"].get("companyName"),
                "last_price": market_state["snapshot"].get("lastPrice"),
                "source": market_state["snapshot"].get("source"),
            },
        )
        return True

    def _symbol_refresh_seconds(self, market: str) -> int:
        if _is_market_session_now(market):
            return max(int(self._settings.overseas_equity_symbol_min_interval_seconds), 1)
        return max(int(self._settings.overseas_equity_offsession_symbol_min_interval_seconds), 1)

    @staticmethod
    def _build_realtime_intraday_bar(market_state: dict[str, dict[str, object]]) -> dict[str, object] | None:
        tick = market_state.get("tick") or {}
        snapshot = market_state.get("snapshot") or {}
        if snapshot.get("source") == "stooq-eod":
            return None

        raw_ts = tick.get("ts") or snapshot.get("updatedAt")
        tick_ts = _coerce_datetime(raw_ts)
        if tick_ts is None:
            return None

        market = str(snapshot.get("market") or _infer_overseas_market(str(tick.get("symbol") or snapshot.get("symbol") or ""))).upper()
        local_ts = tick_ts.astimezone(_market_timezone(market))
        if not _is_market_session_minute(local_ts, market):
            return None

        price = tick.get("price")
        if not isinstance(price, (int, float)):
            return None
        symbol = tick.get("symbol") or snapshot.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            return None

        bucket_ts = local_ts.replace(second=0, microsecond=0).astimezone(UTC)
        volume = tick.get("volume") if isinstance(tick.get("volume"), int) else None
        raw_amount = tick.get("amount")
        amount = float(raw_amount) if isinstance(raw_amount, (int, float)) else None
        normalized_price = float(price)
        return {
            "bucketTs": bucket_ts,
            "symbol": symbol,
            "period": "1m",
            "open": normalized_price,
            "high": normalized_price,
            "low": normalized_price,
            "close": normalized_price,
            "volume": volume,
            "amount": amount,
            "source": OVERSEAS_REALTIME_SOURCE,
            "raw": {
                "provider": OVERSEAS_REALTIME_SOURCE,
                "quality": "realtime_aggregated",
                "synthetic": True,
                "filledBy": "overseas-equity-collector",
                "vendorSnapshotSource": snapshot.get("source"),
                "market": market,
            },
        }


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _infer_overseas_market(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if normalized.endswith(".HK") or normalized.startswith("HK"):
        return "HK"
    return "US"


def _market_timezone(market: str) -> ZoneInfo:
    return HK_MARKET_TZ if market == "HK" else US_MARKET_TZ


def _is_market_session_now(market: str) -> bool:
    return _is_market_session_minute(datetime.now(UTC).astimezone(_market_timezone(market)), market)


def _is_market_session_minute(local_ts: datetime, market: str) -> bool:
    if local_ts.weekday() >= 5:
        return False
    minutes = local_ts.hour * 60 + local_ts.minute
    if market == "HK":
        return (9 * 60 + 30 <= minutes < 12 * 60) or (13 * 60 <= minutes < 16 * 60)
    return 9 * 60 + 30 <= minutes < 16 * 60
