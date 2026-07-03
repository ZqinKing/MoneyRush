from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta, timezone

from redis.asyncio import Redis

from collector.services.capital_flow_client import CapitalFlowClient, CapitalFlowClientError
from collector.services.persistence import PostgresStore
from collector.services.vendor_scheduler import VendorScheduler


logger = logging.getLogger(__name__)
CHINA_MARKET_TZ = timezone(timedelta(hours=8))
CAPITAL_FLOW_JOB_NAME = "capital-flow-daily"
CAPITAL_FLOW_SOURCE = "eastmoney-push2his"


def _coerce_trade_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _is_weekend(value: date) -> bool:
    return value.weekday() >= 5


def _coerce_optional_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _coerce_int(value: object) -> int:
    return value if isinstance(value, int) else 0


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
            eastmoney_delay_base_url=settings.capital_flow_eastmoney_delay_base_url,
            timeout_seconds=settings.capital_flow_request_timeout_seconds,
            retry_attempts=settings.capital_flow_request_retry_attempts,
            retry_backoff_seconds=settings.capital_flow_request_retry_backoff_seconds,
            akshare_fallback_enabled=settings.capital_flow_akshare_fallback_enabled,
        )
        self._vendor_scheduler = VendorScheduler()
        self._postgres_ready = False

    async def run(self) -> None:
        if not self._settings.capital_flow_collector_enabled:
            logger.info("capital-flow collector disabled")
            return

        logger.info("capital-flow collector worker started")
        while True:
            try:
                await self._ensure_postgres_connection()
                await self._ensure_checkpoint()
                did_work = await self._run_due_checkpoint()
                if not did_work:
                    await asyncio.sleep(self._settings.capital_flow_collector_poll_interval_seconds)
            except Exception:  # noqa: BLE001 - collector must keep retrying
                self._postgres_ready = False
                logger.exception("capital-flow collector loop failed; retrying")
                await asyncio.sleep(self._settings.capital_flow_collector_poll_interval_seconds)

    async def run_once(self, symbols: list[str] | None = None, target_trade_date: date | None = None) -> dict[str, int]:
        await self._ensure_postgres_connection()
        target_symbols = sorted({symbol for symbol in (symbols or await self._fetch_active_symbols()) if symbol})
        resolved_trade_date = target_trade_date or self._expected_trade_date()
        if not target_symbols:
            logger.info("capital-flow run skipped; no target symbols")
            return {"requested": 0, "success": 0, "stale": 0}

        logger.info("capital-flow run started", extra={"symbols": target_symbols, "target_trade_date": resolved_trade_date.isoformat()})
        success_count = 0
        stale_count = 0
        attempted_at = datetime.now(UTC)
        stopped_by_cooldown = False

        for symbol in target_symbols:
            try:
                await asyncio.to_thread(self._vendor_scheduler.wait_for_slot, CAPITAL_FLOW_SOURCE)
                item = await asyncio.to_thread(self._client.fetch_for_trade_date, symbol, resolved_trade_date)
                item["collected_at"] = attempted_at
                item["last_attempt_at"] = attempted_at
                if item.get("trade_date") != resolved_trade_date:
                    item["source_status"] = "stale"
                    item["stale_reason"] = "资金流向数据尚未更新至当前交易日。"
                    stale_count += 1
                    logger.warning(
                        "capital-flow trade date lagged current trade day",
                        extra={"symbol": symbol, "trade_date": str(item.get("trade_date")), "target_trade_date": resolved_trade_date.isoformat()},
                    )
                else:
                    success_count += 1
                    self._vendor_scheduler.record_success(CAPITAL_FLOW_SOURCE)
                await self._postgres.upsert_stock_capital_flow_daily_items([item])
            except CapitalFlowClientError as exc:
                stale_count += 1
                await self._postgres.mark_stock_capital_flow_stale(
                    symbol=symbol,
                    trade_date=resolved_trade_date,
                    attempted_at=attempted_at,
                    reason_message="资金流向源暂不可用，当前展示最近一次可用结果。",
                )
                logger.warning("capital-flow refresh degraded", extra={"symbol": symbol, "error": str(exc)})
            except RuntimeError as exc:
                stale_count += 1
                stopped_by_cooldown = True
                self._vendor_scheduler.record_failure(CAPITAL_FLOW_SOURCE, reason=str(exc))
                await self._postgres.mark_stock_capital_flow_stale(
                    symbol=symbol,
                    trade_date=resolved_trade_date,
                    attempted_at=attempted_at,
                    reason_message="资金流向源处于冷却中，当前展示最近一次可用结果。",
                )
                logger.warning("capital-flow vendor cooldown active", extra={"symbol": symbol, "error": str(exc)})
                break

        logger.info(
            "capital-flow run finished",
            extra={"requested": len(target_symbols), "success": success_count, "stale": stale_count, "stopped_by_cooldown": stopped_by_cooldown},
        )
        return {"requested": len(target_symbols), "success": success_count, "stale": stale_count}

    async def _ensure_checkpoint(self) -> None:
        await self._postgres.ensure_capital_flow_checkpoint(
            job_name=CAPITAL_FLOW_JOB_NAME,
            next_due_at=self._next_due_at(),
        )

    async def _run_due_checkpoint(self) -> bool:
        checkpoints = await self._postgres.fetch_capital_flow_checkpoints()
        now = datetime.now(UTC)
        due_items = [item for item in checkpoints if self._checkpoint_is_due(item, now)]
        if not due_items:
            return False
        await self._execute_checkpoint_job(due_items[0])
        return True

    def _checkpoint_is_due(self, checkpoint: dict[str, object], now: datetime) -> bool:
        next_due_at = _coerce_optional_datetime(checkpoint.get("next_due_at"))
        cooldown_until = _coerce_optional_datetime(checkpoint.get("cooldown_until"))
        return (
            checkpoint.get("job_name") == CAPITAL_FLOW_JOB_NAME
            and next_due_at is not None
            and next_due_at <= now
            and (cooldown_until is None or cooldown_until <= now)
        )

    async def _execute_checkpoint_job(self, checkpoint: dict[str, object]) -> None:
        started_at = datetime.now(UTC)
        target_trade_date = self._resolve_target_trade_date(checkpoint)
        due_state = self._classify_target_trade_date_state(target_trade_date)
        if due_state in {"not_due", "no_trade_day", "skipped_outside_window"}:
            await self._record_non_actionable_checkpoint(checkpoint, started_at=started_at, target_trade_date=target_trade_date, status=due_state)
            return

        result = await self.run_once(target_trade_date=target_trade_date)
        finished_at = datetime.now(UTC)
        requested = int(result.get("requested") or 0)
        stale = int(result.get("stale") or 0)
        success = int(result.get("success") or 0)
        result_meta: dict[str, object] = {"requested": requested, "success": success, "stale": stale}
        if requested > 0 and stale == 0 and success == requested:
            await self._postgres.upsert_capital_flow_checkpoint(
                job_name=CAPITAL_FLOW_JOB_NAME,
                next_due_at=self._next_due_at(reference=finished_at),
                cooldown_until=None,
                last_success_at=finished_at,
                last_attempt_at=finished_at,
                last_collected_trade_date=target_trade_date,
                failure_count=0,
                last_error=None,
            )
            await self._postgres.insert_capital_flow_collection_log(
                job_name=CAPITAL_FLOW_JOB_NAME,
                status="success",
                started_at=started_at,
                finished_at=finished_at,
                trade_date=target_trade_date,
                error_message=None,
                meta=result_meta,
            )
            return

        failure_count = _coerce_int(checkpoint.get("failure_count")) + 1
        grace_deadline = self._due_at(target_trade_date).astimezone(UTC) + timedelta(seconds=max(int(self._settings.capital_flow_no_data_grace_seconds), 0))
        if finished_at >= grace_deadline and failure_count > 1:
            status = "unavailable"
            next_due_at = self._next_due_at(reference=finished_at)
            cooldown_until = None
            last_collected_trade_date = target_trade_date
            next_failure_count = 0
        else:
            status = "not_published_yet" if finished_at < grace_deadline else "failure"
            cooldown_until = finished_at + self._cooldown_delta(failure_count)
            next_due_at = cooldown_until
            last_collected_trade_date = _coerce_trade_date(checkpoint.get("last_collected_trade_date"))
            next_failure_count = failure_count

        await self._postgres.upsert_capital_flow_checkpoint(
            job_name=CAPITAL_FLOW_JOB_NAME,
            next_due_at=next_due_at,
            cooldown_until=cooldown_until,
            last_success_at=_coerce_optional_datetime(checkpoint.get("last_success_at")),
            last_attempt_at=finished_at,
            last_collected_trade_date=last_collected_trade_date,
            failure_count=next_failure_count,
            last_error=status,
        )
        await self._postgres.insert_capital_flow_collection_log(
            job_name=CAPITAL_FLOW_JOB_NAME,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            trade_date=target_trade_date,
            error_message=status,
            meta=result_meta,
        )

    async def _record_non_actionable_checkpoint(self, checkpoint: dict[str, object], *, started_at: datetime, target_trade_date: date, status: str) -> None:
        finished_at = datetime.now(UTC)
        last_collected_trade_date = _coerce_trade_date(checkpoint.get("last_collected_trade_date"))
        if status in {"no_trade_day", "skipped_outside_window"}:
            last_collected_trade_date = target_trade_date
        next_due_at = self._next_due_at() if status == "not_due" else self._next_due_at(reference=finished_at)
        await self._postgres.upsert_capital_flow_checkpoint(
            job_name=CAPITAL_FLOW_JOB_NAME,
            next_due_at=next_due_at,
            cooldown_until=None,
            last_success_at=_coerce_optional_datetime(checkpoint.get("last_success_at")),
            last_attempt_at=finished_at,
            last_collected_trade_date=last_collected_trade_date,
            failure_count=_coerce_int(checkpoint.get("failure_count")),
            last_error=None,
        )
        await self._postgres.insert_capital_flow_collection_log(
            job_name=CAPITAL_FLOW_JOB_NAME,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            trade_date=target_trade_date,
            error_message=None,
            meta={"dueState": status},
        )

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect()
        self._postgres_ready = True
        logger.info("capital-flow collector connected to postgres")

    async def _fetch_active_symbols(self) -> list[str]:
        active_symbols = await self._redis.smembers(self._settings.active_symbols_key)
        return [symbol for symbol in active_symbols if isinstance(symbol, str) and symbol.isdigit() and len(symbol) == 6]

    def _expected_trade_date(self) -> date:
        china_now = datetime.now(CHINA_MARKET_TZ)
        due_local = self._due_at(china_now.date())
        if china_now < due_local:
            return china_now.date() - timedelta(days=1)
        return china_now.date()

    def _resolve_target_trade_date(self, checkpoint: dict[str, object]) -> date:
        last_collected = _coerce_trade_date(checkpoint.get("last_collected_trade_date"))
        current_china_date = datetime.now(CHINA_MARKET_TZ).date()
        if last_collected is None:
            return current_china_date
        if last_collected < current_china_date:
            return self._advance_to_next_candidate_date(last_collected)
        return current_china_date

    def _advance_to_next_candidate_date(self, current: date) -> date:
        candidate = current + timedelta(days=1)
        while _is_weekend(candidate):
            candidate += timedelta(days=1)
        return candidate

    def _classify_target_trade_date_state(self, target_trade_date: date) -> str:
        if _is_weekend(target_trade_date):
            return "no_trade_day"
        current_china_date = datetime.now(CHINA_MARKET_TZ).date()
        if target_trade_date < current_china_date - timedelta(days=max(int(self._settings.capital_flow_backfill_window_days), 0)):
            return "skipped_outside_window"
        now_china = datetime.now(CHINA_MARKET_TZ)
        if target_trade_date >= now_china.date() and now_china < self._due_at(target_trade_date):
            return "not_due"
        return "due"

    def _next_due_at(self, *, reference: datetime | None = None) -> datetime:
        reference_time = reference or datetime.now(UTC)
        china_reference = reference_time.astimezone(CHINA_MARKET_TZ)
        due_local = self._due_at(china_reference.date())
        if china_reference >= due_local:
            due_local += timedelta(days=1)
        return due_local.astimezone(UTC)

    def _due_at(self, value: date) -> datetime:
        return datetime.combine(
            value,
            time(
                hour=self._settings.capital_flow_collection_start_hour_china,
                minute=self._settings.capital_flow_collection_start_minute_china,
            ),
            tzinfo=CHINA_MARKET_TZ,
        )

    def _cooldown_delta(self, failure_count: int) -> timedelta:
        base_seconds = max(int(self._settings.capital_flow_collector_poll_interval_seconds), 60)
        return timedelta(seconds=min(base_seconds * (2 ** max(failure_count - 1, 0)), 6 * 3600))
