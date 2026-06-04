from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta, timezone

from redis.asyncio import Redis

from collector.services.capital_flow_client import CapitalFlowClient, CapitalFlowClientError
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)
CHINA_MARKET_TZ = timezone(timedelta(hours=8))


class CapitalFlowCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = CapitalFlowClient(
            eastmoney_base_url=settings.capital_flow_eastmoney_base_url,
            timeout_seconds=settings.capital_flow_request_timeout_seconds,
            retry_attempts=settings.capital_flow_request_retry_attempts,
            retry_backoff_seconds=settings.capital_flow_request_retry_backoff_seconds,
            akshare_fallback_enabled=settings.capital_flow_akshare_fallback_enabled,
        )
        self._postgres_ready = False
        self._last_completed_trade_date: date | None = None

    async def run(self) -> None:
        if not self._settings.capital_flow_collector_enabled:
            logger.info("capital-flow collector disabled")
            return

        logger.info("capital-flow collector worker started")
        while True:
            try:
                await self._ensure_postgres_connection()
                if self._should_run_now():
                    await self.run_once()
                    await asyncio.sleep(self._settings.capital_flow_collector_poll_interval_seconds)
                else:
                    await asyncio.sleep(self._settings.capital_flow_collector_poll_interval_seconds)
            except Exception:  # noqa: BLE001 - collector must keep retrying
                self._postgres_ready = False
                logger.exception("capital-flow collector loop failed; retrying")
                await asyncio.sleep(self._settings.capital_flow_collector_poll_interval_seconds)

    async def run_once(self, symbols: list[str] | None = None) -> dict[str, int]:
        await self._ensure_postgres_connection()
        target_symbols = sorted({symbol for symbol in (symbols or await self._fetch_active_symbols()) if symbol})
        target_trade_date = self._expected_trade_date()
        if not target_symbols:
            logger.info("capital-flow run skipped; no target symbols")
            return {"requested": 0, "success": 0, "stale": 0}

        logger.info("capital-flow run started", extra={"symbols": target_symbols})
        success_count = 0
        stale_count = 0
        attempted_at = datetime.now(UTC)

        for symbol in target_symbols:
            try:
                item = await asyncio.to_thread(self._client.fetch_latest, symbol)
                item["collected_at"] = attempted_at
                item["last_attempt_at"] = attempted_at
                if item.get("trade_date") != target_trade_date:
                    item["source_status"] = "stale"
                    item["stale_reason"] = "资金流向数据尚未更新至当前交易日。"
                    stale_count += 1
                    logger.warning(
                        "capital-flow trade date lagged current trade day",
                        extra={"symbol": symbol, "trade_date": str(item.get("trade_date")), "target_trade_date": target_trade_date.isoformat()},
                    )
                else:
                    success_count += 1
                await self._postgres.upsert_stock_capital_flow_daily_items([item])
            except CapitalFlowClientError as exc:
                stale_count += 1
                await self._postgres.mark_latest_stock_capital_flow_stale(
                    symbol=symbol,
                    trade_date=target_trade_date,
                    attempted_at=attempted_at,
                    reason_message="资金流向源暂不可用，当前展示最近一次可用结果。",
                )
                logger.warning("capital-flow refresh degraded", extra={"symbol": symbol, "error": str(exc)})

        if target_symbols and stale_count == 0:
            self._last_completed_trade_date = target_trade_date
        logger.info(
            "capital-flow run finished",
            extra={"requested": len(target_symbols), "success": success_count, "stale": stale_count},
        )
        return {"requested": len(target_symbols), "success": success_count, "stale": stale_count}

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect()
        self._postgres_ready = True
        logger.info("capital-flow collector connected to postgres")

    async def _fetch_active_symbols(self) -> list[str]:
        active_symbols = await self._redis.smembers(self._settings.active_symbols_key)
        return [symbol for symbol in active_symbols if isinstance(symbol, str) and symbol.isdigit() and len(symbol) == 6]

    def _should_run_now(self) -> bool:
        china_now = datetime.now(CHINA_MARKET_TZ)
        due_local = datetime.combine(
            china_now.date(),
            time(
                hour=self._settings.capital_flow_collection_start_hour_china,
                minute=self._settings.capital_flow_collection_start_minute_china,
            ),
            tzinfo=CHINA_MARKET_TZ,
        )
        if china_now < due_local:
            return False
        return self._last_completed_trade_date != china_now.date()

    def _expected_trade_date(self) -> date:
        china_now = datetime.now(CHINA_MARKET_TZ)
        due_local = datetime.combine(
            china_now.date(),
            time(
                hour=self._settings.capital_flow_collection_start_hour_china,
                minute=self._settings.capital_flow_collection_start_minute_china,
            ),
            tzinfo=CHINA_MARKET_TZ,
        )
        if china_now < due_local:
            return china_now.date() - timedelta(days=1)
        return china_now.date()
