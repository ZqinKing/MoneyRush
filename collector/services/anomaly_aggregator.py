from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import asyncpg


CHINA_MARKET_TZ = timezone(timedelta(hours=8))
SEVERITY_PRIORITY = {"critical": 3, "high": 2, "medium": 1, "normal": 0}


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
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


def _severity_from_percent(value: float | None) -> str:
    absolute_value = abs(value) if isinstance(value, float) else None
    if absolute_value is not None and absolute_value > 5:
        return "critical"
    if absolute_value is not None and absolute_value > 3:
        return "high"
    if absolute_value is not None and absolute_value > 2:
        return "medium"
    return "normal"


def _max_severity(*values: str) -> str:
    return max(values, key=lambda item: SEVERITY_PRIORITY.get(item, 0), default="normal")


def _event_identity(payload: dict[str, object]) -> tuple[object, ...] | None:
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


class AnomalyAggregator:
    def __init__(self, postgres_store, *, ai_reason_enabled: bool = False) -> None:
        self._postgres = postgres_store
        self._ai_reason_status = "pending" if ai_reason_enabled else "skipped"

    async def aggregate_daily_anomalies(self, symbols: list[str], trade_day: date | None = None) -> int:
        unique_symbols = sorted({symbol for symbol in symbols if symbol})
        if not unique_symbols:
            return 0

        target_day = trade_day or datetime.now(CHINA_MARKET_TZ).date()
        day_start_utc, day_end_utc = self._trade_day_window(target_day)
        rows = await self._postgres.fetch_records(
            """
            SELECT se.ts, se.symbol, se.payload, ss.payload AS snapshot_payload
            FROM stock_event se
            LEFT JOIN stock_snapshot ss ON ss.symbol = se.symbol
            WHERE se.symbol = ANY($1::text[])
              AND se.ts >= $2
              AND se.ts < $3
            ORDER BY se.symbol ASC, se.ts ASC
            """,
            unique_symbols,
            day_start_utc,
            day_end_utc,
        )
        snapshot_rows = await self._postgres.fetch_records(
            """
            SELECT symbol, payload
            FROM stock_snapshot
            WHERE symbol = ANY($1::text[])
            """,
            unique_symbols,
        )
        volume_rows = await self._postgres.fetch_records(
            """
            SELECT symbol, bucket_ts, volume
            FROM stock_kline
            WHERE symbol = ANY($1::text[])
              AND period = '1d'
              AND volume IS NOT NULL
            ORDER BY symbol ASC, bucket_ts DESC
            """,
            unique_symbols,
        )

        snapshots = self._snapshots_by_symbol(snapshot_rows)
        average_volumes = self._average_daily_volumes_by_symbol(volume_rows, day_start_utc, day_end_utc)
        latest_volumes = self._latest_daily_volumes_by_symbol(volume_rows, day_start_utc, day_end_utc)
        anomalies = self._build_anomalies(
            symbols=unique_symbols,
            rows=rows,
            snapshots=snapshots,
            average_volumes=average_volumes,
            latest_volumes=latest_volumes,
            target_day=target_day,
        )
        await self._postgres.upsert_significant_anomalies(anomalies)
        return len(anomalies)

    @staticmethod
    def _trade_day_window(target_day: date) -> tuple[datetime, datetime]:
        day_start_local = datetime.combine(target_day, datetime.min.time(), tzinfo=CHINA_MARKET_TZ)
        day_end_local = day_start_local + timedelta(days=1)
        return day_start_local.astimezone(UTC), day_end_local.astimezone(UTC)

    @staticmethod
    def _snapshots_by_symbol(rows: list[asyncpg.Record]) -> dict[str, dict[str, object]]:
        return {str(row["symbol"]): (_decode_jsonish(row["payload"]) or {}) for row in rows}

    @staticmethod
    def _average_daily_volumes_by_symbol(rows: list[asyncpg.Record], day_start_utc: datetime, day_end_utc: datetime) -> dict[str, float]:
        volumes_by_symbol: dict[str, list[int]] = {}
        for row in rows:
            symbol = str(row["symbol"])
            bucket_ts = row["bucket_ts"]
            volume = _to_int(row["volume"])
            if not isinstance(bucket_ts, datetime) or volume is None:
                continue
            if day_start_utc <= bucket_ts < day_end_utc:
                continue
            volumes = volumes_by_symbol.setdefault(symbol, [])
            if len(volumes) < 20:
                volumes.append(volume)
        return {symbol: sum(volumes) / len(volumes) for symbol, volumes in volumes_by_symbol.items() if volumes}

    @staticmethod
    def _latest_daily_volumes_by_symbol(rows: list[asyncpg.Record], day_start_utc: datetime, day_end_utc: datetime) -> dict[str, int]:
        latest_volumes: dict[str, int] = {}
        for row in rows:
            symbol = str(row["symbol"])
            bucket_ts = row["bucket_ts"]
            volume = _to_int(row["volume"])
            if not isinstance(bucket_ts, datetime) or volume is None:
                continue
            if day_start_utc <= bucket_ts < day_end_utc and symbol not in latest_volumes:
                latest_volumes[symbol] = volume
        return latest_volumes

    @staticmethod
    def _build_anomalies(
        *,
        symbols: list[str],
        rows: list[asyncpg.Record],
        snapshots: dict[str, dict[str, object]],
        average_volumes: dict[str, float],
        latest_volumes: dict[str, int],
        target_day: date,
    ) -> list[dict[str, object]]:
        grouped_rows: dict[str, list[asyncpg.Record]] = {}
        for row in rows:
            grouped_rows.setdefault(str(row["symbol"]), []).append(row)

        anomalies: list[dict[str, object]] = []
        for symbol in symbols:
            symbol_rows = grouped_rows.get(symbol, [])
            snapshot = snapshots.get(symbol, {})
            snapshot_price = _to_float(snapshot.get("lastPrice"))
            latest_price = snapshot_price
            change_pct = _to_float(snapshot.get("changePct"))
            latest_volume = latest_volumes.get(symbol)
            previous_price: float | None = None
            current_event_price: float | None = None
            strongest_jump_pct: float | None = None
            trigger_price: float | None = None
            reference_price: float | None = None
            first_trigger_ts: datetime | None = None
            last_trigger_ts: datetime | None = None
            event_count = 0
            previous_identity: tuple[object, ...] | None = None

            for row in symbol_rows:
                payload = _decode_jsonish(row["payload"]) or {}
                tick = payload.get("tick") if isinstance(payload.get("tick"), dict) else {}
                price = _to_float(tick.get("price")) if isinstance(tick, dict) else None
                volume = _to_int(tick.get("volume")) if isinstance(tick, dict) else None
                identity = _event_identity(payload)
                if identity != previous_identity:
                    event_count += 1
                    previous_identity = identity
                if price is not None:
                    if current_event_price is None:
                        current_event_price = price
                    elif price != current_event_price:
                        previous_price = current_event_price
                        current_event_price = price
                        latest_price = price
                        if previous_price:
                            jump_pct = ((price - previous_price) / previous_price) * 100
                            if strongest_jump_pct is None or abs(jump_pct) > abs(strongest_jump_pct):
                                strongest_jump_pct = jump_pct
                                trigger_price = price
                                reference_price = previous_price
                                first_trigger_ts = row["ts"]
                            last_trigger_ts = row["ts"]
                if volume is not None:
                    latest_volume = volume

            average_volume = average_volumes.get(symbol)
            volume_ratio = latest_volume / average_volume if average_volume and latest_volume is not None else None
            jump_severity = _severity_from_percent(strongest_jump_pct)
            change_severity = _severity_from_percent(change_pct)
            volume_severity = "high" if isinstance(volume_ratio, float) and volume_ratio >= 3 else "normal"
            severity = _max_severity(jump_severity, change_severity, volume_severity)
            if severity == "normal":
                continue

            anomaly_type = "price_jump"
            anomaly_change_pct = strongest_jump_pct if strongest_jump_pct is not None else change_pct
            if volume_severity != "normal" and SEVERITY_PRIORITY[volume_severity] >= SEVERITY_PRIORITY[jump_severity]:
                anomaly_type = "volume_spike"
            trigger_ts = first_trigger_ts or last_trigger_ts or datetime.now(UTC)
            last_ts = last_trigger_ts or trigger_ts
            duration_minutes = max(int((last_ts - trigger_ts).total_seconds() // 60), 0)
            first_trigger_bucket = trigger_ts.replace(minute=0, second=0, microsecond=0)
            anomalies.append(
                {
                    "anomaly_date": target_day,
                    "symbol": symbol,
                    "anomaly_type": anomaly_type,
                    "severity": severity,
                    "trigger_price": trigger_price or latest_price,
                    "reference_price": reference_price,
                    "change_pct": anomaly_change_pct,
                    "trigger_volume": latest_volume,
                    "volume_ratio": volume_ratio,
                    "first_trigger_ts": trigger_ts,
                    "last_trigger_ts": last_ts,
                    "duration_minutes": duration_minutes,
                    "event_count": max(event_count, 1),
                    "source": "collector-anomaly-aggregator",
                    "ai_reason_status": self._ai_reason_status,
                    "first_trigger_bucket": first_trigger_bucket,
                    "payload": {
                        "snapshot": snapshot,
                        "strongestPriceJumpPct": strongest_jump_pct,
                        "changePct": change_pct,
                        "volumeRatio": volume_ratio,
                    },
                }
            )
        return anomalies
