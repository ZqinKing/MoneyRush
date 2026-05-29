from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import asyncpg


CHINA_MARKET_TZ = timezone(timedelta(hours=8))
INTRADAY_EXPECTED_BUCKET_COUNT = 240
ANOMALY_SEVERITY_PRIORITY = {"critical": 3, "high": 2, "medium": 1, "normal": 0}


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


def _parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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


def _decode_jsonish_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


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
    return max(values, key=lambda item: ANOMALY_SEVERITY_PRIORITY.get(item, 0), default="normal")


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

    async def fetch_daily_anomaly_report(
        self,
        *,
        symbols: list[str],
        active_funds: list[str] | None = None,
        report_date: str | None = None,
        severities: set[str] | None = None,
        portfolio_only: bool = False,
        sort_by: str = "relevance",
    ) -> dict[str, object]:
        unique_symbols = sorted({symbol for symbol in symbols if symbol})
        unique_active_funds = sorted({fund_code for fund_code in active_funds or [] if fund_code})
        if report_date:
            try:
                day_start_utc, day_end_utc = self._trade_day_window_for_date_label(report_date)
            except ValueError:
                day_start_utc, day_end_utc = self._current_trade_day_window()
        else:
            day_start_utc, day_end_utc = self._current_trade_day_window()

        allowed_severities = severities or {"critical", "high"}
        if not unique_symbols:
            return self._empty_daily_anomaly_report(day_start_utc, allowed_severities, generated_for_count=0)

        persisted_report = await self._fetch_persisted_daily_anomaly_report(
            symbols=unique_symbols,
            active_funds=unique_active_funds,
            day_start_utc=day_start_utc,
            allowed_severities=allowed_severities,
            portfolio_only=portfolio_only,
            sort_by=sort_by,
        )
        if persisted_report is not None:
            return persisted_report

        rows = await self._fetch(
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
        snapshot_rows = await self._fetch(
            """
            SELECT symbol, payload
            FROM stock_snapshot
            WHERE symbol = ANY($1::text[])
            """,
            unique_symbols,
        )
        volume_rows = await self._fetch(
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
        fund_rows = []
        if unique_active_funds:
            fund_rows = await self._fetch(
                """
            WITH latest_report AS (
                SELECT stock_symbol, MAX(report_date) AS report_date
                FROM stock_fund_holding
                WHERE stock_symbol = ANY($1::text[])
                  AND fund_code = ANY($2::text[])
                GROUP BY stock_symbol
            ), ranked AS (
                SELECT sfh.stock_symbol, sfh.fund_code, sfh.fund_name, sfh.fund_type, sfh.report_date,
                       sfh.weight_percent, sfh.hold_market_value, sfh.change_type, sfh.raw,
                       ROW_NUMBER() OVER (
                           PARTITION BY sfh.stock_symbol, sfh.fund_code
                           ORDER BY sfh.weight_percent DESC NULLS LAST, sfh.hold_market_value DESC NULLS LAST
                       ) AS row_choice
                FROM stock_fund_holding sfh
                JOIN latest_report lr
                  ON lr.stock_symbol = sfh.stock_symbol
                 AND lr.report_date = sfh.report_date
                WHERE sfh.fund_code = ANY($2::text[])
            )
            SELECT stock_symbol, fund_code, fund_name, fund_type, report_date, weight_percent,
                   hold_market_value, change_type, raw
            FROM ranked
            WHERE row_choice = 1
            ORDER BY stock_symbol ASC, weight_percent DESC NULLS LAST, fund_code ASC
            """,
                unique_symbols,
                unique_active_funds,
            )

        average_volumes = self._average_daily_volumes_by_symbol(volume_rows, day_start_utc, day_end_utc)
        latest_volumes = self._latest_daily_volumes_by_symbol(volume_rows, day_start_utc, day_end_utc)
        snapshots = self._snapshots_by_symbol(snapshot_rows)
        related_funds = self._related_funds_by_symbol(fund_rows)
        anomaly_candidates = self._build_daily_anomaly_candidates(
            symbols=unique_symbols,
            rows=rows,
            snapshots=snapshots,
            average_volumes=average_volumes,
            latest_volumes=latest_volumes,
            related_funds=related_funds,
        )
        filtered_candidates = [
            item for item in anomaly_candidates
            if item["severity"] in allowed_severities and (not portfolio_only or item["relatedFunds"])
        ]
        sorted_candidates = self._sort_daily_anomalies(filtered_candidates, sort_by)
        portfolio_anomalies = [item for item in sorted_candidates if item["relatedFunds"]]
        other_anomalies = [] if portfolio_only else [item for item in sorted_candidates if not item["relatedFunds"]]

        severity_counts = {"critical": 0, "high": 0, "medium": 0}
        for item in sorted_candidates:
            severity = item.get("severity")
            if severity in severity_counts:
                severity_counts[severity] += 1

        return {
            "date": self._trade_day_label(day_start_utc),
            "generatedAt": datetime.now(UTC).isoformat(),
            "summary": {
                "totalAnomalyCount": len(sorted_candidates),
                "portfolioAnomalyCount": len(portfolio_anomalies),
                "otherAnomalyCount": len(other_anomalies),
                "criticalCount": severity_counts["critical"],
                "highCount": severity_counts["high"],
                "mediumCount": severity_counts["medium"],
                "activeSymbolCount": len(unique_symbols),
                "requestedSeverities": sorted(allowed_severities),
            },
            "portfolioAnomalies": portfolio_anomalies,
            "otherAnomalies": other_anomalies,
            "stableHoldings": [],
        }

    async def _fetch_persisted_daily_anomaly_report(
        self,
        *,
        symbols: list[str],
        active_funds: list[str],
        day_start_utc: datetime,
        allowed_severities: set[str],
        portfolio_only: bool,
        sort_by: str,
    ) -> dict[str, object] | None:
        anomaly_date = day_start_utc.astimezone(CHINA_MARKET_TZ).date()
        try:
            anomaly_rows = await self._fetch(
                """
                SELECT sa.*, ss.payload AS snapshot_payload
                FROM significant_anomaly sa
                LEFT JOIN stock_snapshot ss ON ss.symbol = sa.symbol
                WHERE sa.symbol = ANY($1::text[])
                  AND sa.anomaly_date = $2::date
                  AND sa.severity = ANY($3::text[])
                ORDER BY sa.first_trigger_ts DESC
                """,
                symbols,
                anomaly_date,
                sorted(allowed_severities),
            )
        except asyncpg.UndefinedTableError:
            return None

        if not anomaly_rows:
            return None

        fund_rows = []
        if active_funds:
            fund_rows = await self._fetch(
                """
            WITH latest_report AS (
                SELECT stock_symbol, MAX(report_date) AS report_date
                FROM stock_fund_holding
                WHERE stock_symbol = ANY($1::text[])
                  AND fund_code = ANY($2::text[])
                GROUP BY stock_symbol
            ), ranked AS (
                SELECT sfh.stock_symbol, sfh.fund_code, sfh.fund_name, sfh.fund_type, sfh.report_date,
                       sfh.weight_percent, sfh.hold_market_value, sfh.change_type,
                       ROW_NUMBER() OVER (
                           PARTITION BY sfh.stock_symbol, sfh.fund_code
                           ORDER BY sfh.weight_percent DESC NULLS LAST, sfh.hold_market_value DESC NULLS LAST
                       ) AS row_choice
                FROM stock_fund_holding sfh
                JOIN latest_report lr
                  ON lr.stock_symbol = sfh.stock_symbol
                 AND lr.report_date = sfh.report_date
                WHERE sfh.fund_code = ANY($2::text[])
            )
            SELECT stock_symbol, fund_code, fund_name, fund_type, report_date, weight_percent,
                   hold_market_value, change_type
            FROM ranked
            WHERE row_choice = 1
            ORDER BY stock_symbol ASC, weight_percent DESC NULLS LAST, fund_code ASC
            """,
                symbols,
                active_funds,
            )
        related_funds = self._related_funds_by_symbol(fund_rows)
        items: list[dict[str, object]] = []
        for row in anomaly_rows:
            symbol = str(row["symbol"])
            snapshot = _decode_jsonish(row["snapshot_payload"]) or {}
            change_pct = _to_float(row["change_pct"])
            symbol_funds = self._funds_with_estimated_impact(related_funds.get(symbol, []), change_pct)
            if portfolio_only and not symbol_funds:
                continue
            items.append(
                {
                    "symbol": symbol,
                    "stockName": snapshot.get("companyName") or symbol,
                    "anomalyType": row["anomaly_type"],
                    "severity": row["severity"],
                    "changePct": change_pct,
                    "latestPriceJumpPct": change_pct if row["anomaly_type"] == "price_jump" else None,
                    "triggerPrice": _to_float(row["trigger_price"]),
                    "triggerTime": _to_iso(row["first_trigger_ts"]),
                    "volumeRatio": _to_float(row["volume_ratio"]),
                    "eventCountToday": _to_int(row["event_count"]) or 0,
                    "relatedFunds": symbol_funds,
                    "impactEstimate": (
                        "持仓相关，已按最近披露仓位估算单项影响"
                        if symbol_funds
                        else "监控标的出现显著变化，未匹配到最近披露基金持仓"
                    ),
                    "aiReason": row["ai_reason"],
                    "aiReasonStatus": "skipped" if row["ai_reason"] is None and row["ai_reason_status"] == "pending" else row["ai_reason_status"],
                    "aiReasonGeneratedAt": _to_iso(row["ai_reason_generated_at"]),
                    "relatedNewsIds": _decode_jsonish_list(row["related_news_ids"]),
                    "relatedAnnouncementIds": _decode_jsonish_list(row["related_announcement_ids"]),
                }
            )
        sorted_items = self._sort_daily_anomalies(items, sort_by)
        portfolio_anomalies = [item for item in sorted_items if item["relatedFunds"]]
        other_anomalies = [] if portfolio_only else [item for item in sorted_items if not item["relatedFunds"]]
        severity_counts = {"critical": 0, "high": 0, "medium": 0}
        for item in sorted_items:
            severity = item.get("severity")
            if severity in severity_counts:
                severity_counts[severity] += 1

        return {
            "date": anomaly_date.isoformat(),
            "generatedAt": datetime.now(UTC).isoformat(),
            "summary": {
                "totalAnomalyCount": len(sorted_items),
                "portfolioAnomalyCount": len(portfolio_anomalies),
                "otherAnomalyCount": len(other_anomalies),
                "criticalCount": severity_counts["critical"],
                "highCount": severity_counts["high"],
                "mediumCount": severity_counts["medium"],
                "activeSymbolCount": len(symbols),
                "requestedSeverities": sorted(allowed_severities),
                "source": "significant_anomaly",
            },
            "portfolioAnomalies": portfolio_anomalies,
            "otherAnomalies": other_anomalies,
            "stableHoldings": [],
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

    async def fetch_intraday_sampled_bars(
        self,
        symbol: str,
        *,
        interval_minutes: int = 5,
        allow_tick_fallback: bool = True,
    ) -> list[dict[str, object]]:
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

        if not allow_tick_fallback:
            return []

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

    async def fetch_intraday_completeness(
        self,
        symbol: str,
        *,
        reference_ts: object = None,
        reconciliation_seconds: int = 0,
    ) -> dict[str, object]:
        reference_dt = reference_ts if isinstance(reference_ts, datetime) else _parse_iso_timestamp(reference_ts)
        trade_day_window = (
            self._trade_day_window_for_ts(reference_dt)
            if isinstance(reference_dt, datetime)
            else await self._latest_intraday_trade_day_window(symbol)
        )

        if trade_day_window is None:
            return {
                "tradeDay": None,
                "expectedFinalBucketTs": None,
                "lastBucketTs": None,
                "expectedBucketCount": INTRADAY_EXPECTED_BUCKET_COUNT,
                "actualBucketCount": 0,
                "missingBucketCount": INTRADAY_EXPECTED_BUCKET_COUNT,
                "isComplete": False,
                "isFinal": True,
                "status": "unavailable",
                "source": None,
            }

        day_start_utc, day_end_utc = trade_day_window
        rows = await self._fetch(
            """
            SELECT bucket_ts, source
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

        trade_day_label = self._trade_day_label(day_start_utc)
        expected_buckets = self._expected_intraday_bucket_set(day_start_utc)
        actual_buckets = {
            row["bucket_ts"]
            for row in rows
            if isinstance(row["bucket_ts"], datetime)
        }
        actual_count = len(actual_buckets)
        missing_count = max(len(expected_buckets) - len(actual_buckets), 0)
        expected_final_bucket = max(expected_buckets) if expected_buckets else None
        last_bucket = max(actual_buckets) if actual_buckets else None
        is_complete = expected_buckets.issubset(actual_buckets) if expected_buckets else False
        status = self._resolve_intraday_status(
            trade_day_window=trade_day_window,
            is_complete=is_complete,
            has_rows=bool(actual_buckets),
            reconciliation_seconds=reconciliation_seconds,
        )
        return {
            "tradeDay": trade_day_label,
            "expectedFinalBucketTs": _to_iso(expected_final_bucket),
            "lastBucketTs": _to_iso(last_bucket),
            "expectedBucketCount": len(expected_buckets),
            "actualBucketCount": actual_count,
            "missingBucketCount": missing_count,
            "isComplete": is_complete,
            "isFinal": status != "pending",
            "status": status,
            "source": rows[-1]["source"] if rows else None,
        }

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
    def _empty_daily_anomaly_report(
        day_start_utc: datetime,
        allowed_severities: set[str],
        *,
        generated_for_count: int,
    ) -> dict[str, object]:
        return {
            "date": MarketDetailQueryService._trade_day_label(day_start_utc),
            "generatedAt": datetime.now(UTC).isoformat(),
            "summary": {
                "totalAnomalyCount": 0,
                "portfolioAnomalyCount": 0,
                "otherAnomalyCount": 0,
                "criticalCount": 0,
                "highCount": 0,
                "mediumCount": 0,
                "activeSymbolCount": generated_for_count,
                "requestedSeverities": sorted(allowed_severities),
            },
            "portfolioAnomalies": [],
            "otherAnomalies": [],
            "stableHoldings": [],
        }

    @staticmethod
    def _average_daily_volumes_by_symbol(
        rows: list[asyncpg.Record],
        day_start_utc: datetime,
        day_end_utc: datetime,
    ) -> dict[str, float]:
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
    def _latest_daily_volumes_by_symbol(
        rows: list[asyncpg.Record],
        day_start_utc: datetime,
        day_end_utc: datetime,
    ) -> dict[str, int]:
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
    def _snapshots_by_symbol(rows: list[asyncpg.Record]) -> dict[str, dict[str, object]]:
        return {
            str(row["symbol"]): (_decode_jsonish(row["payload"]) or {})
            for row in rows
        }

    @staticmethod
    def _related_funds_by_symbol(rows: list[asyncpg.Record]) -> dict[str, list[dict[str, object]]]:
        funds_by_symbol: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            symbol = str(row["stock_symbol"])
            funds_by_symbol.setdefault(symbol, []).append(
                {
                    "fundCode": row["fund_code"],
                    "fundName": row["fund_name"],
                    "fundType": row["fund_type"],
                    "reportDate": row["report_date"].isoformat() if row["report_date"] is not None else None,
                    "stockWeightInFund": _to_float(row["weight_percent"]),
                    "holdMarketValue": _to_float(row["hold_market_value"]),
                    "changeType": row["change_type"],
                }
            )
        return funds_by_symbol

    @staticmethod
    def _build_daily_anomaly_candidates(
        *,
        symbols: list[str],
        rows: list[asyncpg.Record],
        snapshots: dict[str, dict[str, object]],
        average_volumes: dict[str, float],
        latest_volumes: dict[str, int],
        related_funds: dict[str, list[dict[str, object]]],
    ) -> list[dict[str, object]]:
        grouped_rows: dict[str, list[asyncpg.Record]] = {}
        for row in rows:
            grouped_rows.setdefault(str(row["symbol"]), []).append(row)

        candidates: list[dict[str, object]] = []
        for symbol in symbols:
            symbol_rows = grouped_rows.get(symbol, [])
            event_count = 0
            previous_identity: tuple[object, ...] | None = None
            previous_distinct_price: float | None = None
            latest_price: float | None = None
            latest_volume: int | None = None
            latest_event_ts: str | None = None
            strongest_jump_pct: float | None = None
            trigger_price: float | None = None
            trigger_time: str | None = None
            stock_name: str | None = None
            snapshot_payload = snapshots.get(symbol, {})
            snapshot_change_pct: float | None = _to_float(snapshot_payload.get("changePct"))
            snapshot_latest_price = _to_float(snapshot_payload.get("lastPrice"))
            latest_volume = latest_volumes.get(symbol)
            stock_name = str(snapshot_payload.get("companyName") or symbol)

            for row in symbol_rows:
                payload = _decode_jsonish(row["payload"]) or {}
                event_snapshot_payload = _decode_jsonish(row["snapshot_payload"]) or {}
                event_snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
                tick = payload.get("tick") if isinstance(payload.get("tick"), dict) else {}
                price = _to_float(tick.get("price")) if isinstance(tick, dict) else None
                volume = _to_int(tick.get("volume")) if isinstance(tick, dict) else None
                event_identity = _event_summary_identity(payload)

                if event_identity != previous_identity:
                    event_count += 1
                    previous_identity = event_identity

                stock_name = (
                    stock_name
                    or (event_snapshot.get("companyName") if isinstance(event_snapshot, dict) else None)
                    or event_snapshot_payload.get("companyName")
                    or symbol
                )
                snapshot_change_pct = snapshot_change_pct if snapshot_change_pct is not None else _to_float(event_snapshot_payload.get("changePct"))
                if snapshot_change_pct is None and isinstance(event_snapshot, dict):
                    snapshot_change_pct = _to_float(event_snapshot.get("changePct"))

                if price is not None:
                    if latest_price is None:
                        latest_price = price
                    elif price != latest_price:
                        previous_distinct_price = latest_price
                        latest_price = price
                        if previous_distinct_price:
                            jump_pct = ((latest_price - previous_distinct_price) / previous_distinct_price) * 100
                            if strongest_jump_pct is None or abs(jump_pct) > abs(strongest_jump_pct):
                                strongest_jump_pct = jump_pct
                                trigger_price = latest_price
                                trigger_time = _to_iso(row["ts"])

                if volume is not None:
                    latest_volume = volume
                latest_event_ts = _to_iso(row["ts"])

            average_volume = average_volumes.get(symbol)
            volume_ratio = latest_volume / average_volume if average_volume and latest_volume is not None else None
            jump_severity = _severity_from_percent(strongest_jump_pct)
            change_severity = _severity_from_percent(snapshot_change_pct)
            volume_severity = "high" if isinstance(volume_ratio, float) and volume_ratio >= 3 else "normal"
            severity = _max_severity(jump_severity, change_severity, volume_severity)
            if severity == "normal":
                continue

            anomaly_type = "price_jump"
            if volume_severity != "normal" and ANOMALY_SEVERITY_PRIORITY[volume_severity] >= ANOMALY_SEVERITY_PRIORITY[jump_severity]:
                anomaly_type = "volume_spike"

            symbol_funds = MarketDetailQueryService._funds_with_estimated_impact(related_funds.get(symbol, []), snapshot_change_pct)
            candidates.append(
                {
                    "symbol": symbol,
                    "stockName": stock_name or symbol,
                    "anomalyType": anomaly_type,
                    "severity": severity,
                    "changePct": snapshot_change_pct,
                    "latestPriceJumpPct": strongest_jump_pct,
                    "triggerPrice": trigger_price or latest_price or snapshot_latest_price,
                    "triggerTime": trigger_time or latest_event_ts,
                    "volumeRatio": volume_ratio,
                    "eventCountToday": event_count,
                    "relatedFunds": symbol_funds,
                    "impactEstimate": (
                        "持仓相关，需结合基金整体仓位评估"
                        if symbol_funds
                        else "监控标的出现显著变化，未匹配到最近披露基金持仓"
                    ),
                }
            )
        return candidates

    @staticmethod
    def _sort_daily_anomalies(items: list[dict[str, object]], sort_by: str) -> list[dict[str, object]]:
        if sort_by == "time":
            return sorted(items, key=lambda item: str(item.get("triggerTime") or ""), reverse=True)
        if sort_by == "magnitude":
            return sorted(
                items,
                key=lambda item: max(
                    abs(item["changePct"]) if isinstance(item.get("changePct"), float) else 0,
                    abs(item["latestPriceJumpPct"]) if isinstance(item.get("latestPriceJumpPct"), float) else 0,
                    item["volumeRatio"] if isinstance(item.get("volumeRatio"), float) else 0,
                ),
                reverse=True,
            )
        return sorted(
            items,
            key=lambda item: (
                1 if item.get("relatedFunds") else 0,
                ANOMALY_SEVERITY_PRIORITY.get(str(item.get("severity")), 0),
                abs(item["changePct"]) if isinstance(item.get("changePct"), float) else 0,
            ),
            reverse=True,
        )

    @staticmethod
    def _funds_with_estimated_impact(funds: list[dict[str, object]], change_pct: float | None) -> list[dict[str, object]]:
        enriched_funds: list[dict[str, object]] = []
        for fund in funds:
            weight = fund.get("stockWeightInFund")
            estimated_impact = None
            if isinstance(weight, float) and isinstance(change_pct, float):
                estimated_impact = weight * change_pct / 100
            enriched_funds.append({**fund, "estimatedImpact": estimated_impact})
        return enriched_funds

    @staticmethod
    def _current_trade_day_window() -> tuple[datetime, datetime]:
        now_local = datetime.now(CHINA_MARKET_TZ)
        day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_local = day_start_local + timedelta(days=1)
        return day_start_local.astimezone(UTC), day_end_local.astimezone(UTC)

    @staticmethod
    def _trade_day_window_for_date_label(value: str) -> tuple[datetime, datetime]:
        parsed = datetime.fromisoformat(value)
        day_start_local = parsed.replace(tzinfo=CHINA_MARKET_TZ, hour=0, minute=0, second=0, microsecond=0)
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
    def _expected_intraday_bucket_set(day_start_utc: datetime) -> set[datetime]:
        local_day = day_start_utc.astimezone(CHINA_MARKET_TZ)
        morning_start = local_day.replace(hour=9, minute=30, second=0, microsecond=0)
        afternoon_start = local_day.replace(hour=13, minute=0, second=0, microsecond=0)
        buckets = {
            (morning_start + timedelta(minutes=offset)).astimezone(UTC)
            for offset in range(120)
        }
        buckets.update(
            (afternoon_start + timedelta(minutes=offset)).astimezone(UTC)
            for offset in range(120)
        )
        return buckets

    @staticmethod
    def _resolve_intraday_status(
        *,
        trade_day_window: tuple[datetime, datetime],
        is_complete: bool,
        has_rows: bool,
        reconciliation_seconds: int,
    ) -> str:
        if is_complete:
            return "complete"
        if not has_rows:
            return "unavailable"

        now_local = datetime.now(CHINA_MARKET_TZ)
        trade_day_local = trade_day_window[0].astimezone(CHINA_MARKET_TZ)
        close_local = trade_day_local.replace(hour=15, minute=0, second=0, microsecond=0)
        reconciliation_end_local = close_local + timedelta(seconds=max(reconciliation_seconds, 0))
        if now_local.date() == trade_day_local.date() and now_local <= reconciliation_end_local:
            return "pending"
        return "incomplete"

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
