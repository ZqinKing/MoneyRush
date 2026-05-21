from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import asyncpg


CHINA_MARKET_TZ = timezone(timedelta(hours=8))


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int, Decimal)):
        return float(value)
    return None


def _to_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, float):
        return int(value)
    return None


def _to_iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _decode_jsonish(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _event_identity(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _event_summary_identity(payload: dict[str, object]) -> tuple[object, ...] | None:
    tick = payload.get("tick") if isinstance(payload.get("tick"), dict) else None
    kline = payload.get("kline") if isinstance(payload.get("kline"), dict) else None
    if tick is None and kline is None:
        return None

    return (
        _to_float(tick.get("price")) if tick else None,
        _to_int(tick.get("volume")) if tick else None,
        tick.get("side") if tick else None,
        kline.get("period") if kline else None,
        _to_float(kline.get("close")) if kline else None,
        _to_float(kline.get("high")) if kline else None,
        _to_float(kline.get("low")) if kline else None,
    )


def _expanded_unique_scan_limit(limit: int) -> int:
    return min(max(limit * 10, limit), 1000)


class MarketDetailQueryService:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def fetch_snapshot(self, symbol: str) -> dict[str, object] | None:
        row = await self._fetchrow(
            """
            SELECT payload, updated_at
            FROM stock_snapshot
            WHERE symbol = $1
            """,
            symbol,
        )
        if row is None:
            return None

        snapshot = _decode_jsonish(row["payload"]) or {}
        updated_at = _to_iso(row["updated_at"])
        if updated_at is not None:
            snapshot["updatedAt"] = updated_at
        return snapshot or None

    async def fetch_latest_event(self, symbol: str) -> dict[str, object] | None:
        row = await self._fetchrow(
            """
            SELECT ts, payload
            FROM stock_event
            WHERE symbol = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            symbol,
        )
        if row is None:
            return None

        event_payload = _decode_jsonish(row["payload"]) or {}
        generated_at = event_payload.get("generatedAt")
        if generated_at is None:
            event_payload["generatedAt"] = _to_iso(row["ts"])
        return event_payload or None

    async def fetch_latest_kline(self, symbol: str, period: str = "1d") -> dict[str, object] | None:
        row = await self._fetchrow(
            """
            SELECT bucket_ts, period, open, high, low, close, volume, amount, source
            FROM stock_kline
            WHERE symbol = $1 AND period = $2
            ORDER BY bucket_ts DESC
            LIMIT 1
            """,
            symbol,
            period,
        )
        return self._serialize_kline_row(row)

    async def fetch_klines(self, symbol: str, period: str = "1d", limit: int = 1) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT bucket_ts, period, open, high, low, close, volume, amount, source
            FROM stock_kline
            WHERE symbol = $1 AND period = $2
            ORDER BY bucket_ts DESC
            LIMIT $3
            """,
            symbol,
            period,
            limit,
        )
        return [item for item in (self._serialize_kline_row(row) for row in rows) if item is not None]

    async def fetch_ticks(self, symbol: str, limit: int) -> list[dict[str, object]]:
        raw_limit = _expanded_unique_scan_limit(limit)
        rows = await self._fetch(
            """
            SELECT ts, price, volume, amount, side, source
            FROM stock_tick
            WHERE symbol = $1
            ORDER BY ts DESC
            LIMIT $2
            """,
            symbol,
            raw_limit,
        )
        items: list[dict[str, object]] = []
        seen: set[tuple[object, ...]] = set()

        for row in rows:
            item = {
                "ts": _to_iso(row["ts"]),
                "price": _to_float(row["price"]),
                "volume": _to_int(row["volume"]),
                "amount": _to_float(row["amount"]),
                "side": row["side"],
                "source": row["source"],
            }
            identity = (
                item["ts"],
                item["price"],
                item["volume"],
                item["amount"],
                item["side"],
                item["source"],
            )
            if identity in seen:
                continue
            seen.add(identity)
            items.append(item)
            if len(items) >= limit:
                break

        return items

    async def fetch_events(self, symbol: str, limit: int) -> list[dict[str, object]]:
        raw_limit = _expanded_unique_scan_limit(limit)
        rows = await self._fetch(
            """
            SELECT ts, event_type, source, payload
            FROM stock_event
            WHERE symbol = $1
            ORDER BY ts DESC
            LIMIT $2
            """,
            symbol,
            raw_limit,
        )
        items: list[dict[str, object]] = []
        seen: set[tuple[object, ...]] = set()

        for row in rows:
            payload = _decode_jsonish(row["payload"]) or {}
            item = {
                "ts": _to_iso(row["ts"]),
                "eventType": row["event_type"],
                "source": row["source"],
                "payload": payload,
            }
            identity = (
                item["ts"],
                item["eventType"],
                item["source"],
                _event_identity(payload),
            )
            if identity in seen:
                continue
            seen.add(identity)
            items.append(item)
            if len(items) >= limit:
                break

        return items

    async def fetch_event_summary(self, symbol: str) -> dict[str, object]:
        day_start_utc, day_end_utc = self._current_trade_day_window()
        rows = await self._fetch(
            """
            SELECT ts, payload
            FROM stock_event
            WHERE symbol = $1
              AND ts >= $2
              AND ts < $3
            ORDER BY ts ASC
            """,
            symbol,
            day_start_utc,
            day_end_utc,
        )

        event_count = 0
        buy_count = 0
        sell_count = 0
        directed_count = 0
        previous_distinct_price: float | None = None
        latest_price: float | None = None
        latest_volume: int | None = None
        latest_event_ts: str | None = None
        latest_jump_pct: float | None = None
        previous_event_identity: tuple[object, ...] | None = None

        for row in rows:
            payload = _decode_jsonish(row["payload"]) or {}
            tick = payload.get("tick") if isinstance(payload.get("tick"), dict) else {}
            side = tick.get("side") if isinstance(tick, dict) else None
            price = _to_float(tick.get("price")) if isinstance(tick, dict) else None
            volume = _to_int(tick.get("volume")) if isinstance(tick, dict) else None
            event_identity = _event_summary_identity(payload)

            if event_identity != previous_event_identity:
                event_count += 1
                if side == "buy":
                    buy_count += 1
                    directed_count += 1
                elif side == "sell":
                    sell_count += 1
                    directed_count += 1
                previous_event_identity = event_identity

            if price is not None:
                if latest_price is None:
                    latest_price = price
                elif price != latest_price:
                    previous_distinct_price = latest_price
                    latest_price = price

            if volume is not None:
                latest_volume = volume

            latest_event_ts = _to_iso(row["ts"])

        history_rows = await self._fetch(
            """
            SELECT bucket_ts, volume
            FROM stock_kline
            WHERE symbol = $1
              AND period = '1d'
              AND volume IS NOT NULL
            ORDER BY bucket_ts DESC
            LIMIT 40
            """,
            symbol,
        )

        history_volumes: list[int] = []
        for row in history_rows:
            bucket_ts = row["bucket_ts"]
            volume = _to_int(row["volume"])
            if not isinstance(bucket_ts, datetime) or volume is None:
                continue
            if day_start_utc <= bucket_ts < day_end_utc:
                continue
            history_volumes.append(volume)
            if len(history_volumes) >= 20:
                break

        average_daily_volume = sum(history_volumes) / len(history_volumes) if history_volumes else None
        volume_ratio = None
        if average_daily_volume and average_daily_volume > 0 and latest_volume is not None:
            volume_ratio = latest_volume / average_daily_volume

        if latest_price is not None and previous_distinct_price is not None and previous_distinct_price != 0:
            latest_jump_pct = ((latest_price - previous_distinct_price) / previous_distinct_price) * 100

        absolute_jump_pct = abs(latest_jump_pct) if isinstance(latest_jump_pct, float) else None
        if absolute_jump_pct is not None and absolute_jump_pct > 5:
            jump_severity = "critical"
        elif absolute_jump_pct is not None and absolute_jump_pct > 3:
            jump_severity = "high"
        else:
            jump_severity = "normal"

        return {
            "symbol": symbol,
            "tradeDay": self._trade_day_label(day_start_utc),
            "eventCountToday": event_count,
            "buyCount": buy_count,
            "sellCount": sell_count,
            "buyRatio": _safe_ratio(buy_count, directed_count),
            "sellRatio": _safe_ratio(sell_count, directed_count),
            "latestPrice": latest_price,
            "latestVolume": latest_volume,
            "averageDailyVolume20": average_daily_volume,
            "volumeRatio": volume_ratio,
            "latestPriceJumpPct": latest_jump_pct,
            "jumpSeverity": jump_severity,
            "latestEventTs": latest_event_ts,
        }

    async def fetch_best_bid_ask(self, symbol: str) -> dict[str, object]:
        row = await self._fetchrow(
            """
            SELECT raw
            FROM stock_tick
            WHERE symbol = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            symbol,
        )
        raw = _decode_jsonish(row["raw"]) if row is not None else None
        return {
            "bid1": _to_float(raw.get("bid1")) if raw else None,
            "bidVolume1": _to_int(raw.get("bidVolume1")) if raw else None,
            "ask1": _to_float(raw.get("ask1")) if raw else None,
            "askVolume1": _to_int(raw.get("askVolume1")) if raw else None,
        }

    async def fetch_intraday_sampled_bars(self, symbol: str, *, interval_minutes: int = 5) -> list[dict[str, object]]:
        if interval_minutes < 1:
            raise ValueError("interval_minutes must be >= 1")

        latest_trade_day_window = await self._latest_intraday_trade_day_window(symbol)
        if latest_trade_day_window is None:
            return []

        persisted_bars = await self._fetch_intraday_bars_from_kline(
            symbol,
            trade_day_window=latest_trade_day_window,
            interval_minutes=interval_minutes,
        )
        if persisted_bars:
            return persisted_bars

        day_start_utc, day_end_utc = latest_trade_day_window
        source_priority_rows = await self._fetch(
            """
            SELECT source, COUNT(*) AS row_count, MAX(ts) AS last_ts
            FROM stock_tick
            WHERE symbol = $1
              AND ts >= $2
              AND ts < $3
            GROUP BY source
            ORDER BY
              CASE
                WHEN source = 'mootdx' THEN 0
                WHEN source = 'tencent-finance' THEN 1
                ELSE 2
              END,
              MAX(ts) DESC
            """,
            symbol,
            day_start_utc,
            day_end_utc,
        )
        latest_source = source_priority_rows[0]["source"] if source_priority_rows else None
        if not isinstance(latest_source, str):
            return []

        rows = await self._fetch(
            """
            SELECT ts, price, volume, amount, source
            FROM stock_tick
            WHERE symbol = $1
              AND ts >= $2
              AND ts < $3
              AND source = $4
            ORDER BY ts ASC
            """,
            symbol,
            day_start_utc,
            day_end_utc,
            latest_source,
        )

        bars: list[dict[str, object]] = []
        current_bar: dict[str, object] | None = None
        current_bucket: datetime | None = None
        previous_volume: int | None = None
        previous_amount: float | None = None

        for row in rows:
            ts = row["ts"]
            price = _to_float(row["price"])
            volume = _to_int(row["volume"])
            amount = _to_float(row["amount"])
            source = row["source"]

            if not isinstance(ts, datetime) or price is None:
                continue

            bucket_ts = self._bucket_intraday_ts(ts, interval_minutes)
            volume_delta = 0
            amount_delta = 0.0

            if volume is not None and previous_volume is not None and volume >= previous_volume:
                volume_delta = volume - previous_volume
            if amount is not None and previous_amount is not None and amount >= previous_amount:
                amount_delta = amount - previous_amount

            if bucket_ts != current_bucket:
                current_bucket = bucket_ts
                current_bar = {
                    "bucketTs": _to_iso(bucket_ts),
                    "period": f"sampled-{interval_minutes}m",
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": volume_delta,
                    "amount": amount_delta,
                    "source": source,
                }
                bars.append(current_bar)
            elif current_bar is not None:
                current_bar["high"] = max(current_bar["high"], price)
                current_bar["low"] = min(current_bar["low"], price)
                current_bar["close"] = price
                current_bar["volume"] += volume_delta
                current_bar["amount"] += amount_delta
                current_bar["source"] = source

            if volume is not None:
                previous_volume = volume
            if amount is not None:
                previous_amount = amount

        return bars

    async def _fetch_intraday_bars_from_kline(
        self,
        symbol: str,
        *,
        trade_day_window: tuple[datetime, datetime],
        interval_minutes: int,
    ) -> list[dict[str, object]]:
        day_start_utc, day_end_utc = trade_day_window
        rows = await self._fetch(
            """
            SELECT bucket_ts, open, high, low, close, volume, amount, source
            FROM stock_kline
            WHERE symbol = $1
              AND period = '1m'
              AND bucket_ts >= $2
              AND bucket_ts < $3
            ORDER BY bucket_ts ASC
            """,
            symbol,
            day_start_utc,
            day_end_utc,
        )
        if not rows:
            return []

        bars: list[dict[str, object]] = []
        current_bucket: datetime | None = None
        current_bar: dict[str, object] | None = None

        for row in rows:
            bucket_ts = row["bucket_ts"]
            open_price = _to_float(row["open"])
            high_price = _to_float(row["high"])
            low_price = _to_float(row["low"])
            close_price = _to_float(row["close"])
            volume = _to_int(row["volume"])
            amount = _to_float(row["amount"])
            source = row["source"]

            if not isinstance(bucket_ts, datetime) or None in (open_price, high_price, low_price, close_price):
                continue

            sampled_bucket_ts = self._bucket_intraday_ts(bucket_ts, interval_minutes)
            if sampled_bucket_ts != current_bucket:
                current_bucket = sampled_bucket_ts
                current_bar = {
                    "bucketTs": _to_iso(sampled_bucket_ts),
                    "period": f"sampled-{interval_minutes}m",
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume or 0,
                    "amount": amount or 0.0,
                    "source": source,
                }
                bars.append(current_bar)
                continue

            if current_bar is None:
                continue

            current_bar["high"] = max(current_bar["high"], high_price)
            current_bar["low"] = min(current_bar["low"], low_price)
            current_bar["close"] = close_price
            current_bar["volume"] += volume or 0
            current_bar["amount"] += amount or 0.0
            current_bar["source"] = source

        return bars

    async def _latest_intraday_trade_day_window(self, symbol: str) -> tuple[datetime, datetime] | None:
        latest_tick_row = await self._fetchrow(
            """
            SELECT ts
            FROM stock_tick
            WHERE symbol = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            symbol,
        )
        latest_kline_row = await self._fetchrow(
            """
            SELECT bucket_ts
            FROM stock_kline
            WHERE symbol = $1 AND period = '1m'
            ORDER BY bucket_ts DESC
            LIMIT 1
            """,
            symbol,
        )

        candidates: list[datetime] = []
        if latest_tick_row is not None and isinstance(latest_tick_row["ts"], datetime):
            candidates.append(latest_tick_row["ts"])
        if latest_kline_row is not None and isinstance(latest_kline_row["bucket_ts"], datetime):
            candidates.append(latest_kline_row["bucket_ts"])

        if not candidates:
            return None

        latest_ts = max(candidates)
        return self._trade_day_window_for_ts(latest_ts)

    async def _fetchrow(self, query: str, *args: object) -> asyncpg.Record | None:
        if self._pool is None:
            raise RuntimeError("MarketDetailQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("MarketDetailQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, *args)

    @staticmethod
    def _current_trade_day_window() -> tuple[datetime, datetime]:
        now_local = datetime.now(CHINA_MARKET_TZ)
        day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_local = day_start_local + timedelta(days=1)
        return day_start_local.astimezone(UTC), day_end_local.astimezone(UTC)

    @staticmethod
    def _trade_day_window_for_ts(ts: datetime) -> tuple[datetime, datetime]:
        local_ts = ts.astimezone(CHINA_MARKET_TZ)
        day_start_local = local_ts.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_local = day_start_local + timedelta(days=1)
        return day_start_local.astimezone(UTC), day_end_local.astimezone(UTC)

    @staticmethod
    def _trade_day_label(day_start_utc: datetime) -> str:
        local_day = day_start_utc.astimezone(CHINA_MARKET_TZ)
        return local_day.date().isoformat()

    @staticmethod
    def _bucket_intraday_ts(ts: datetime, interval_minutes: int) -> datetime:
        local_ts = ts.astimezone(CHINA_MARKET_TZ)
        bucket_minute = (local_ts.minute // interval_minutes) * interval_minutes
        bucket_local = local_ts.replace(minute=bucket_minute, second=0, microsecond=0)
        return bucket_local.astimezone(UTC)

    @staticmethod
    def _serialize_kline_row(row: asyncpg.Record | None) -> dict[str, object] | None:
        if row is None:
            return None
        return {
            "bucketTs": _to_iso(row["bucket_ts"]),
            "period": row["period"],
            "open": _to_float(row["open"]),
            "high": _to_float(row["high"]),
            "low": _to_float(row["low"]),
            "close": _to_float(row["close"]),
            "volume": _to_int(row["volume"]),
            "amount": _to_float(row["amount"]),
            "source": row["source"],
        }
