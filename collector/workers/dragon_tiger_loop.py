from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta, timezone

from redis.asyncio import Redis

from collector.services.dragon_tiger_client import DragonTigerClient, DragonTigerClientError
from collector.services.persistence import PostgresStore


logger = logging.getLogger(__name__)
CHINA_MARKET_TZ = timezone(timedelta(hours=8))
DRAGON_TIGER_JOB_NAME = "dragon-tiger-daily"


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


class DragonTigerCollectorWorker:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._postgres = PostgresStore(
            settings.postgres_dsn,
            enable_runtime_data_repair=settings.collector_enable_runtime_data_repair,
        )
        self._client = DragonTigerClient(
            timeout_seconds=settings.dragon_tiger_request_timeout_seconds,
            retry_attempts=settings.dragon_tiger_request_retry_attempts,
            retry_backoff_seconds=settings.dragon_tiger_request_retry_backoff_seconds,
        )
        self._postgres_ready = False

    async def run(self) -> None:
        if not self._settings.dragon_tiger_collector_enabled:
            logger.info("dragon-tiger collector disabled")
            return

        logger.info("dragon-tiger collector worker started")
        while True:
            try:
                await self._ensure_postgres_connection()
                await self._ensure_checkpoint()
                did_work = await self._run_once()
                if not did_work:
                    await asyncio.sleep(self._settings.dragon_tiger_collector_poll_interval_seconds)
            except Exception:
                self._postgres_ready = False
                logger.exception("dragon-tiger collector loop failed; retrying")
                await asyncio.sleep(self._settings.dragon_tiger_collector_poll_interval_seconds)

    async def _ensure_postgres_connection(self) -> None:
        if self._postgres_ready:
            return
        await self._postgres.connect()
        self._postgres_ready = True
        logger.info("dragon-tiger collector connected to postgres")

    async def _ensure_checkpoint(self) -> None:
        await self._postgres.ensure_dragon_tiger_checkpoint(
            job_name=DRAGON_TIGER_JOB_NAME,
            next_due_at=self._next_due_at(),
        )

    async def _run_once(self) -> bool:
        checkpoints = await self._postgres.fetch_dragon_tiger_checkpoints()
        now = datetime.now(UTC)
        due_items = [
            item
            for item in checkpoints
            if item.get("job_name") == DRAGON_TIGER_JOB_NAME
            and isinstance(item.get("next_due_at"), datetime)
            and item["next_due_at"] <= now
            and (item.get("cooldown_until") is None or item["cooldown_until"] <= now)
        ]
        if not due_items:
            return False

        await self._execute_job(due_items[0])
        return True

    async def _execute_job(self, checkpoint: dict[str, object]) -> None:
        started_at = datetime.now(UTC)
        target_trade_date = self._resolve_target_trade_date(checkpoint)
        due_state = self._classify_target_trade_date_state(target_trade_date)
        if due_state in {"not_due", "no_trade_day"}:
            finished_at = datetime.now(UTC)
            next_due_at = self._next_due_at() if due_state == "not_due" else self._next_due_at(reference=finished_at)
            last_collected_trade_date = checkpoint.get("last_collected_trade_date")
            if due_state == "no_trade_day":
                last_collected_trade_date = target_trade_date
            await self._postgres.upsert_dragon_tiger_checkpoint(
                job_name=DRAGON_TIGER_JOB_NAME,
                next_due_at=next_due_at,
                cooldown_until=None,
                last_success_at=checkpoint.get("last_success_at"),
                last_attempt_at=finished_at,
                last_collected_trade_date=last_collected_trade_date,
                failure_count=int(checkpoint.get("failure_count") or 0),
                last_error=None,
            )
            await self._postgres.insert_dragon_tiger_collection_log(
                job_name=DRAGON_TIGER_JOB_NAME,
                status=due_state,
                started_at=started_at,
                finished_at=finished_at,
                trade_date=target_trade_date,
                error_message=None,
                meta={"dueState": due_state},
            )
            logger.info("dragon-tiger collector skipped non-actionable target date", extra={"trade_date": target_trade_date.isoformat(), "due_state": due_state})
            return

        try:
            daily_payload = await asyncio.to_thread(self._client.fetch_daily, trade_date=target_trade_date.isoformat())
            institution_payload = await asyncio.to_thread(
                self._client.fetch_institution_trade_details,
                start_date=target_trade_date.isoformat(),
                end_date=target_trade_date.isoformat(),
            )

            if not daily_payload.get("items") and not institution_payload.get("items"):
                await self._handle_no_rows_after_due(checkpoint, started_at=started_at, target_trade_date=target_trade_date)
                return

            collected_at = datetime.now(UTC)
            await self._postgres.upsert_dragon_tiger_daily_items(
                [
                    {
                        "trade_date": target_trade_date,
                        "symbol": item.get("symbol"),
                        "name": item.get("name"),
                        "close_price": item.get("closePrice"),
                        "change_percent": item.get("changePercent"),
                        "net_buy_amount": item.get("netBuyAmount"),
                        "buy_amount": item.get("buyAmount"),
                        "sell_amount": item.get("sellAmount"),
                        "deal_amount": item.get("dealAmount"),
                        "total_amount": item.get("totalAmount"),
                        "net_buy_ratio": item.get("netBuyRatio"),
                        "deal_amount_ratio": item.get("dealAmountRatio"),
                        "turnover_rate": item.get("turnoverRate"),
                        "free_market_cap": item.get("freeMarketCap"),
                        "explain": item.get("explain"),
                        "reason": item.get("reason"),
                        "after_1d": item.get("after1d"),
                        "after_2d": item.get("after2d"),
                        "after_5d": item.get("after5d"),
                        "after_10d": item.get("after10d"),
                        "source": daily_payload.get("source", "eastmoney-datacenter"),
                        "generated_at": daily_payload.get("generatedAt"),
                        "collected_at": collected_at,
                        "raw_payload": item,
                    }
                    for item in daily_payload.get("items", [])
                    if item.get("symbol")
                ]
            )
            await self._postgres.upsert_dragon_tiger_institution_items(
                [
                    {
                        "trade_date": target_trade_date,
                        "symbol": item.get("symbol"),
                        "name": item.get("name"),
                        "close_price": item.get("closePrice"),
                        "change_percent": item.get("changePercent"),
                        "buy_org_count": item.get("buyOrgCount"),
                        "sell_org_count": item.get("sellOrgCount"),
                        "org_buy_amount": item.get("orgBuyAmount"),
                        "org_sell_amount": item.get("orgSellAmount"),
                        "org_net_amount": item.get("orgNetAmount"),
                        "market_total_amount": item.get("marketTotalAmount"),
                        "org_net_amount_ratio": item.get("orgNetAmountRatio"),
                        "turnover_rate": item.get("turnoverRate"),
                        "free_market_cap": item.get("freeMarketCap"),
                        "reason": item.get("reason"),
                        "source": institution_payload.get("source", "eastmoney-datacenter"),
                        "generated_at": institution_payload.get("generatedAt"),
                        "collected_at": collected_at,
                        "raw_payload": item,
                    }
                    for item in institution_payload.get("items", [])
                    if item.get("symbol")
                ]
            )
            await self._clear_dragon_tiger_caches()

            finished_at = datetime.now(UTC)
            await self._postgres.upsert_dragon_tiger_checkpoint(
                job_name=DRAGON_TIGER_JOB_NAME,
                next_due_at=self._next_due_at(reference=finished_at),
                cooldown_until=None,
                last_success_at=finished_at,
                last_attempt_at=finished_at,
                last_collected_trade_date=target_trade_date,
                failure_count=0,
                last_error=None,
            )
            await self._postgres.insert_dragon_tiger_collection_log(
                job_name=DRAGON_TIGER_JOB_NAME,
                status="success",
                started_at=started_at,
                finished_at=finished_at,
                trade_date=target_trade_date,
                error_message=None,
                meta={
                    "dailyRows": len(daily_payload.get("items", [])),
                    "institutionRows": len(institution_payload.get("items", [])),
                },
            )
        except Exception as exc:
            finished_at = datetime.now(UTC)
            failure_count = int(checkpoint.get("failure_count") or 0) + 1
            cooldown_until = finished_at + self._cooldown_delta(failure_count)
            await self._postgres.upsert_dragon_tiger_checkpoint(
                job_name=DRAGON_TIGER_JOB_NAME,
                next_due_at=cooldown_until,
                cooldown_until=cooldown_until,
                last_success_at=checkpoint.get("last_success_at"),
                last_attempt_at=finished_at,
                last_collected_trade_date=checkpoint.get("last_collected_trade_date"),
                failure_count=failure_count,
                last_error=str(exc),
            )
            await self._postgres.insert_dragon_tiger_collection_log(
                job_name=DRAGON_TIGER_JOB_NAME,
                status="failure",
                started_at=started_at,
                finished_at=finished_at,
                trade_date=target_trade_date,
                error_message=str(exc),
                meta={"failureCount": failure_count},
            )
            logger.exception("dragon-tiger collector job failed", extra={"trade_date": target_trade_date.isoformat()})

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
        now_china = datetime.now(CHINA_MARKET_TZ)
        collection_due = datetime.combine(
            target_trade_date,
            time(
                hour=self._settings.dragon_tiger_collection_start_hour_china,
                minute=self._settings.dragon_tiger_collection_start_minute_china,
            ),
            tzinfo=CHINA_MARKET_TZ,
        )
        if target_trade_date >= now_china.date() and now_china < collection_due:
            return "not_due"
        return "due"

    def _next_due_at(self, *, reference: datetime | None = None) -> datetime:
        reference_time = reference or datetime.now(UTC)
        china_reference = reference_time.astimezone(CHINA_MARKET_TZ)
        due_local = datetime.combine(
            china_reference.date(),
            time(
                hour=self._settings.dragon_tiger_collection_start_hour_china,
                minute=self._settings.dragon_tiger_collection_start_minute_china,
            ),
            tzinfo=CHINA_MARKET_TZ,
        )
        if china_reference >= due_local:
            due_local += timedelta(days=1)
        return due_local.astimezone(UTC)

    def _cooldown_delta(self, failure_count: int) -> timedelta:
        base_seconds = max(int(self._settings.dragon_tiger_collector_poll_interval_seconds), 60)
        return timedelta(seconds=min(base_seconds * (2 ** max(failure_count - 1, 0)), 6 * 3600))

    async def _handle_no_rows_after_due(self, checkpoint: dict[str, object], *, started_at: datetime, target_trade_date: date) -> None:
        finished_at = datetime.now(UTC)
        collection_due_local = datetime.combine(
            target_trade_date,
            time(
                hour=self._settings.dragon_tiger_collection_start_hour_china,
                minute=self._settings.dragon_tiger_collection_start_minute_china,
            ),
            tzinfo=CHINA_MARKET_TZ,
        )
        grace_deadline = collection_due_local.astimezone(UTC) + timedelta(seconds=max(int(self._settings.dragon_tiger_no_data_grace_seconds), 0))
        if finished_at >= grace_deadline:
            status = "unavailable"
            next_due_at = self._next_due_at(reference=finished_at)
            cooldown_until = None
            last_collected_trade_date = target_trade_date
        else:
            status = "not_published_yet"
            next_due_at = finished_at + self._cooldown_delta(1)
            cooldown_until = next_due_at
            last_collected_trade_date = checkpoint.get("last_collected_trade_date")

        await self._postgres.upsert_dragon_tiger_checkpoint(
            job_name=DRAGON_TIGER_JOB_NAME,
            next_due_at=next_due_at,
            cooldown_until=cooldown_until,
            last_success_at=checkpoint.get("last_success_at"),
            last_attempt_at=finished_at,
            last_collected_trade_date=last_collected_trade_date,
            failure_count=int(checkpoint.get("failure_count") or 0),
            last_error=None,
        )
        await self._postgres.insert_dragon_tiger_collection_log(
            job_name=DRAGON_TIGER_JOB_NAME,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            trade_date=target_trade_date,
            error_message=None,
            meta={"dueState": status, "graceDeadline": grace_deadline.isoformat()},
        )
        logger.info("dragon-tiger rows not available after due time", extra={"trade_date": target_trade_date.isoformat(), "due_state": status})

    async def _clear_dragon_tiger_caches(self) -> None:
        cursor = 0
        pattern = "moneyrush:dragon_tiger:*"
        while True:
            cursor, keys = await self._redis.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                await self._redis.delete(*keys)
            if cursor == 0:
                break
