from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import asyncpg


CHINA_MARKET_TZ = timezone(timedelta(hours=8))
INTRADAY_EXPECTED_BUCKET_COUNT = 240
ANOMALY_SEVERITY_PRIORITY = {"critical": 3, "high": 2, "medium": 1, "normal": 0}
TICK_SIDE_BASIS = "price_vs_previous_close"
TICK_SIDE_CONFIDENCE = "estimated"
CAPITAL_FLOW_UNAVAILABLE_SOURCE = "capital-flow-unavailable"
CAPITAL_FLOW_TIER_FIELDS = (
    {"key": "main", "label": "主力", "net_field": "main_net_inflow", "ratio_field": "main_net_ratio"},
    {"key": "superLarge", "label": "超大单", "net_field": "super_large_net_inflow", "ratio_field": "super_large_net_ratio"},
    {"key": "large", "label": "大单", "net_field": "large_net_inflow", "ratio_field": "large_net_ratio"},
    {"key": "medium", "label": "中单", "net_field": "medium_net_inflow", "ratio_field": "medium_net_ratio"},
    {"key": "small", "label": "小单", "net_field": "small_net_inflow", "ratio_field": "small_net_ratio"},
)
CAPITAL_FLOW_PERIOD_WINDOWS = (
    {"period": "1d", "label": "今日", "window": 1},
    {"period": "5d", "label": "5日", "window": 5},
    {"period": "10d", "label": "10日", "window": 10},
)


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


def _raw_float(value: object) -> float | None:
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return _to_float(value)


def _raw_int(value: object) -> int | None:
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return _to_int(value)


def _to_iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _to_iso_date(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except TypeError:
            return None
    return None


def _parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _is_market_session_ts(value: object) -> bool:
    if not isinstance(value, datetime):
        return False
    local_ts = value.astimezone(CHINA_MARKET_TZ) if value.tzinfo else value.replace(tzinfo=UTC).astimezone(CHINA_MARKET_TZ)
    minutes = local_ts.hour * 60 + local_ts.minute
    return 9 * 60 + 30 <= minutes <= 15 * 60


def _snapshot_matches_trade_day(snapshot: dict[str, object], target_day: date) -> bool:
    updated_at = _parse_iso_timestamp(snapshot.get("updatedAt"))
    return updated_at is not None and updated_at.astimezone(CHINA_MARKET_TZ).date() == target_day


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


def _row_get(row: object, key: str) -> object:
    if isinstance(row, dict):
        return row.get(key)
    getter = getattr(row, "__getitem__", None)
    if not callable(getter):
        return None
    try:
        return getter(key)
    except (KeyError, TypeError, IndexError):
        return None


def _capital_flow_has_numeric_tier(row: object) -> bool:
    return any(_to_float(_row_get(row, tier["net_field"])) is not None for tier in CAPITAL_FLOW_TIER_FIELDS)


def _capital_flow_period_row_is_usable(row: object) -> bool:
    return _row_get(row, "source") != CAPITAL_FLOW_UNAVAILABLE_SOURCE and _capital_flow_has_numeric_tier(row)


def _capital_flow_denominator(net_inflow: float | None, ratio: float | None) -> float | None:
    if net_inflow is None or ratio is None or ratio == 0:
        return None
    denominator = abs(net_inflow / (ratio / 100))
    return denominator if denominator > 0 else None


def _serialize_capital_flow_tier(row: object | None, tier: dict[str, str]) -> dict[str, object]:
    if row is None:
        return {"key": tier["key"], "label": tier["label"], "netInflow": None, "ratio": None}
    return {
        "key": tier["key"],
        "label": tier["label"],
        "netInflow": _to_float(_row_get(row, tier["net_field"])),
        "ratio": _to_float(_row_get(row, tier["ratio_field"])),
    }


def _aggregate_capital_flow_tier(rows: list[object], tier: dict[str, str]) -> dict[str, object]:
    net_values = [_to_float(_row_get(row, tier["net_field"])) for row in rows]
    usable_net_values = [value for value in net_values if value is not None]
    net_inflow = sum(usable_net_values) if usable_net_values else None
    denominator = 0.0
    for row in rows:
        row_net_inflow = _to_float(_row_get(row, tier["net_field"]))
        row_ratio = _to_float(_row_get(row, tier["ratio_field"]))
        row_denominator = _capital_flow_denominator(row_net_inflow, row_ratio)
        if row_denominator is not None:
            denominator += row_denominator
    ratio = (net_inflow / denominator * 100) if net_inflow is not None and denominator > 0 else None
    return {"key": tier["key"], "label": tier["label"], "netInflow": net_inflow, "ratio": ratio}


def build_capital_flow_periods(rows: Sequence[object]) -> list[dict[str, object]]:
    usable_rows = [row for row in rows if _capital_flow_period_row_is_usable(row)]
    periods: list[dict[str, object]] = []
    for period_spec in CAPITAL_FLOW_PERIOD_WINDOWS:
        window = int(period_spec["window"])
        period_rows = usable_rows[:window]
        sample_size = len(period_rows)
        start_trade_date = _to_iso_date(_row_get(period_rows[-1], "trade_date")) if period_rows else None
        end_trade_date = _to_iso_date(_row_get(period_rows[0], "trade_date")) if period_rows else None
        tiers = [
            _serialize_capital_flow_tier(period_rows[0], tier) if window == 1 and period_rows else _aggregate_capital_flow_tier(period_rows, tier)
            for tier in CAPITAL_FLOW_TIER_FIELDS
        ]
        periods.append(
            {
                "period": period_spec["period"],
                "label": period_spec["label"],
                "window": window,
                "tradeDate": end_trade_date,
                "startTradeDate": start_trade_date,
                "endTradeDate": end_trade_date,
                "sampleSize": sample_size,
                "complete": sample_size >= window,
                "tiers": tiers,
            }
        )
    return periods


def _tick_side_label(side: object) -> str:
    if side == "buy":
        return "高于/持平昨收"
    if side == "sell":
        return "低于昨收"
    return "--"


def _tick_side_basis(side: object) -> str | None:
    return TICK_SIDE_BASIS if side in {"buy", "sell"} else None


def _tick_side_confidence(side: object) -> str | None:
    return TICK_SIDE_CONFIDENCE if side in {"buy", "sell"} else None


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

    async def fetch_latest_capital_flow(self, symbol: str) -> dict[str, object] | None:
        rows = await self._fetch(
            """
            SELECT
                trade_date,
                symbol,
                company_name,
                main_net_inflow,
                main_net_ratio,
                super_large_net_inflow,
                super_large_net_ratio,
                large_net_inflow,
                large_net_ratio,
                medium_net_inflow,
                medium_net_ratio,
                small_net_inflow,
                small_net_ratio,
                close_price,
                change_pct,
                source,
                source_status,
                generated_at,
                collected_at,
                last_attempt_at,
                stale_reason
            FROM stock_capital_flow_daily
            WHERE symbol = $1
            ORDER BY trade_date DESC
            LIMIT 20
            """,
            symbol,
        )
        if not rows:
            return None

        row = rows[0]
        trade_date = row["trade_date"]
        source_status = row["source_status"]
        is_stale = source_status == "stale"
        stale_reason = row["stale_reason"] if is_stale else None
        return {
            "symbol": row["symbol"],
            "companyName": row["company_name"],
            "tradeDate": _to_iso_date(trade_date),
            "mainNetInflow": _to_float(row["main_net_inflow"]),
            "mainNetRatio": _to_float(row["main_net_ratio"]),
            "superLargeNetInflow": _to_float(row["super_large_net_inflow"]),
            "superLargeNetRatio": _to_float(row["super_large_net_ratio"]),
            "largeNetInflow": _to_float(row["large_net_inflow"]),
            "largeNetRatio": _to_float(row["large_net_ratio"]),
            "mediumNetInflow": _to_float(row["medium_net_inflow"]),
            "mediumNetRatio": _to_float(row["medium_net_ratio"]),
            "smallNetInflow": _to_float(row["small_net_inflow"]),
            "smallNetRatio": _to_float(row["small_net_ratio"]),
            "closePrice": _to_float(row["close_price"]),
            "changePct": _to_float(row["change_pct"]),
            "source": row["source"],
            "sourceStatus": source_status,
            "generatedAt": _to_iso(row["generated_at"]),
            "collectedAt": _to_iso(row["collected_at"]),
            "lastAttemptAt": _to_iso(row["last_attempt_at"]),
            "staleReason": stale_reason,
            "stale": is_stale,
            "periods": build_capital_flow_periods(rows),
        }

    async def fetch_latest_capital_flows(self, symbols: list[str]) -> dict[str, dict[str, object]]:
        normalized_symbols = sorted({symbol for symbol in symbols if symbol})
        if not normalized_symbols:
            return {}

        rows = await self._fetch(
            """
            WITH ranked AS (
                SELECT
                    trade_date,
                    symbol,
                    company_name,
                    main_net_inflow,
                    main_net_ratio,
                    source,
                    source_status,
                    generated_at,
                    collected_at,
                    last_attempt_at,
                    stale_reason,
                    ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS row_choice
                FROM stock_capital_flow_daily
                WHERE symbol = ANY($1::text[])
            )
            SELECT
                trade_date,
                symbol,
                company_name,
                main_net_inflow,
                main_net_ratio,
                source,
                source_status,
                generated_at,
                collected_at,
                last_attempt_at,
                stale_reason
            FROM ranked
            WHERE row_choice = 1
            """,
            normalized_symbols,
        )

        items: dict[str, dict[str, object]] = {}
        for row in rows:
            trade_date = row["trade_date"]
            source_status = row["source_status"]
            items[str(row["symbol"])] = {
                "symbol": row["symbol"],
                "companyName": row["company_name"],
                "tradeDate": _to_iso_date(trade_date),
                "mainNetInflow": _to_float(row["main_net_inflow"]),
                "mainNetRatio": _to_float(row["main_net_ratio"]),
                "source": row["source"],
                "sourceStatus": source_status,
                "generatedAt": _to_iso(row["generated_at"]),
                "collectedAt": _to_iso(row["collected_at"]),
                "lastAttemptAt": _to_iso(row["last_attempt_at"]),
                "staleReason": row["stale_reason"],
                "stale": source_status == "stale",
            }
        return items

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
                "sideLabel": _tick_side_label(row["side"]),
                "sideBasis": _tick_side_basis(row["side"]),
                "sideConfidence": _tick_side_confidence(row["side"]),
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
            "sideBasis": TICK_SIDE_BASIS if directed_count else None,
            "sideConfidence": TICK_SIDE_CONFIDENCE if directed_count else None,
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
                SELECT fund_code, MAX(report_date) AS report_date
                FROM fund_stock_holding
                WHERE fund_code = ANY($2::text[])
                GROUP BY fund_code
            ), ranked AS (
                SELECT fsh.stock_symbol, fsh.fund_code, fp.fund_name, fp.fund_type, fsh.report_date,
                       COALESCE(fsh.weight_percent, sfh.weight_percent) AS weight_percent,
                       fsh.hold_market_value, fsh.change_type, fsh.raw,
                       ROW_NUMBER() OVER (
                           PARTITION BY fsh.stock_symbol, fsh.fund_code
                           ORDER BY fsh.rank ASC NULLS LAST, fsh.weight_percent DESC NULLS LAST, fsh.hold_market_value DESC NULLS LAST
                       ) AS row_choice
                FROM fund_stock_holding fsh
                JOIN latest_report lr
                  ON lr.fund_code = fsh.fund_code
                 AND lr.report_date = fsh.report_date
                LEFT JOIN fund_profile fp ON fp.fund_code = fsh.fund_code
                LEFT JOIN LATERAL (
                    SELECT weight_percent
                    FROM stock_fund_holding
                    WHERE stock_symbol = fsh.stock_symbol
                      AND fund_code = fsh.fund_code
                      AND report_date = fsh.report_date
                      AND weight_percent IS NOT NULL
                    ORDER BY weight_percent DESC
                    LIMIT 1
                ) sfh ON true
                WHERE fsh.stock_symbol = ANY($1::text[])
                  AND fsh.fund_code = ANY($2::text[])
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
            target_day=day_start_utc.astimezone(CHINA_MARKET_TZ).date(),
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
                  AND (sa.first_trigger_ts AT TIME ZONE 'Asia/Shanghai')::time >= TIME '09:30'
                  AND (sa.first_trigger_ts AT TIME ZONE 'Asia/Shanghai')::time <= TIME '15:00'
                  AND (
                      sa.payload #>> '{snapshot,updatedAt}' IS NULL
                      OR LEFT(sa.payload #>> '{snapshot,updatedAt}', 10) = sa.anomaly_date::text
                  )
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

        post_close_reviews = await self._fetch_post_close_review_map(
            trade_date=anomaly_date,
            symbols=sorted({str(row["symbol"]) for row in anomaly_rows if row["symbol"]}),
        )
        fund_rows = []
        if active_funds:
            fund_rows = await self._fetch(
                """
            WITH latest_report AS (
                SELECT fund_code, MAX(report_date) AS report_date
                FROM fund_stock_holding
                WHERE fund_code = ANY($2::text[])
                GROUP BY fund_code
            ), ranked AS (
                SELECT fsh.stock_symbol, fsh.fund_code, fp.fund_name, fp.fund_type, fsh.report_date,
                       COALESCE(fsh.weight_percent, sfh.weight_percent) AS weight_percent,
                       fsh.hold_market_value, fsh.change_type,
                       ROW_NUMBER() OVER (
                           PARTITION BY fsh.stock_symbol, fsh.fund_code
                           ORDER BY fsh.rank ASC NULLS LAST, fsh.weight_percent DESC NULLS LAST, fsh.hold_market_value DESC NULLS LAST
                       ) AS row_choice
                FROM fund_stock_holding fsh
                JOIN latest_report lr
                  ON lr.fund_code = fsh.fund_code
                 AND lr.report_date = fsh.report_date
                LEFT JOIN fund_profile fp ON fp.fund_code = fsh.fund_code
                LEFT JOIN LATERAL (
                    SELECT weight_percent
                    FROM stock_fund_holding
                    WHERE stock_symbol = fsh.stock_symbol
                      AND fund_code = fsh.fund_code
                      AND report_date = fsh.report_date
                      AND weight_percent IS NOT NULL
                    ORDER BY weight_percent DESC
                    LIMIT 1
                ) sfh ON true
                WHERE fsh.stock_symbol = ANY($1::text[])
                  AND fsh.fund_code = ANY($2::text[])
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
            anomaly_payload = _decode_jsonish(row["payload"]) or {}
            change_pct = _to_float(anomaly_payload.get("changePct"))
            if change_pct is None:
                change_pct = _to_float(row["change_pct"])
            latest_price_jump_pct = _to_float(anomaly_payload.get("strongestPriceJumpPct"))
            symbol_funds = self._funds_with_estimated_impact(related_funds.get(symbol, []), change_pct)
            if portfolio_only and not symbol_funds:
                continue
            post_close_review = post_close_reviews.get(symbol)
            post_close_status = row["ai_reason_post_close_status"]
            post_close_generated_at = row["ai_reason_post_close_generated_at"]
            post_close_reason = row["ai_reason_post_close"]
            ai_reason = row["ai_reason"]
            ai_reason_status = row["ai_reason_status"]
            ai_reason_generated_at = row["ai_reason_generated_at"]
            ai_reason_phase = row["ai_reason_phase"]
            ai_reason_includes_dragon_tiger = bool(row["ai_reason_includes_dragon_tiger"])
            related_news_ids = _decode_jsonish_list(row["related_news_ids"])
            related_announcement_ids = _decode_jsonish_list(row["related_announcement_ids"])
            if post_close_review is not None:
                post_close_status = post_close_review["status"]
                post_close_generated_at = post_close_review["generated_at"]
                post_close_reason = post_close_review["reason"]
                ai_reason_includes_dragon_tiger = bool(post_close_review["includes_dragon_tiger"])
                if post_close_review["status"] == "completed":
                    ai_reason = post_close_review["reason"]
                    ai_reason_status = "completed"
                    ai_reason_generated_at = post_close_review["generated_at"]
                    ai_reason_phase = "post_close"
                    related_news_ids = _decode_jsonish_list(post_close_review["related_news_ids"])
                    related_announcement_ids = _decode_jsonish_list(post_close_review["related_announcement_ids"])
            items.append(
                {
                    "symbol": symbol,
                    "stockName": snapshot.get("companyName") or symbol,
                    "anomalyType": row["anomaly_type"],
                    "severity": row["severity"],
                    "changePct": change_pct,
                    "latestPriceJumpPct": latest_price_jump_pct if row["anomaly_type"] == "price_jump" else None,
                    "triggerPrice": _to_float(row["trigger_price"]),
                    "triggerTime": _to_iso(row["first_trigger_ts"]),
                    "firstTriggerBucket": _to_iso(row["first_trigger_bucket"]),
                    "volumeRatio": _to_float(row["volume_ratio"]),
                    "eventCountToday": _to_int(row["event_count"]) or 0,
                    "relatedFunds": symbol_funds,
                    "impactEstimate": (
                        "持仓相关，已按最近披露仓位估算单项影响"
                        if symbol_funds
                        else "监控标的出现显著变化，未匹配到最近披露基金持仓"
                    ),
                    **self._daily_anomaly_ai_fields(
                        reason=ai_reason,
                        status=ai_reason_status,
                        generated_at=ai_reason_generated_at,
                        related_news_ids=related_news_ids,
                        related_announcement_ids=related_announcement_ids,
                        phase=ai_reason_phase,
                        evidence_cutoff_at=row["ai_reason_evidence_cutoff_at"],
                        includes_dragon_tiger=ai_reason_includes_dragon_tiger,
                        post_close_required=bool(row["ai_reason_post_close_required"]),
                        post_close_status=post_close_status,
                        post_close_generated_at=post_close_generated_at,
                        post_close_reason=post_close_reason,
                    ),
                }
            )
        deduped_items = self._dedupe_daily_anomalies_by_symbol(items)
        sorted_items = self._sort_daily_anomalies(deduped_items, sort_by)
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

    async def _fetch_post_close_review_map(self, *, trade_date, symbols: list[str]) -> dict[str, asyncpg.Record]:
        if not symbols:
            return {}
        try:
            rows = await self._fetch(
                """
                SELECT trade_date, symbol, representative_anomaly_id, status, reason, generated_at,
                       evidence_fingerprint, evidence_cutoff_at, includes_dragon_tiger,
                       related_news_ids, related_announcement_ids, attempt_count, next_retry_at, last_error
                FROM anomaly_post_close_review_checkpoint
                WHERE trade_date = $1::date
                  AND symbol = ANY($2::text[])
                """,
                trade_date,
                symbols,
            )
        except asyncpg.UndefinedTableError:
            return {}
        return {str(row["symbol"]): row for row in rows}

    async def fetch_best_bid_ask(self, symbol: str) -> dict[str, object]:
        order_book = await self.fetch_order_book(symbol)
        return {
            "bid1": order_book.get("bid1"),
            "bidVolume1": order_book.get("bidVolume1"),
            "ask1": order_book.get("ask1"),
            "askVolume1": order_book.get("askVolume1"),
        }

    async def fetch_order_book(self, symbol: str) -> dict[str, object]:
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
        return self._order_book_from_raw(raw or {})

    async def fetch_intraday_sampled_bars(
        self,
        symbol: str,
        *,
        interval_minutes: int = 5,
        allow_tick_fallback: bool = True,
        reference_ts: object = None,
    ) -> list[dict[str, object]]:
        if interval_minutes < 1:
            raise ValueError("interval_minutes must be >= 1")

        reference_dt = reference_ts if isinstance(reference_ts, datetime) else _parse_iso_timestamp(reference_ts)
        trade_day_window = (
            self._trade_day_window_for_ts(reference_dt)
            if isinstance(reference_dt, datetime)
            else await self._latest_intraday_trade_day_window(symbol)
        )
        if trade_day_window is None:
            return []

        persisted_bars = await self._fetch_intraday_bars_from_kline(
            symbol,
            trade_day_window=trade_day_window,
            interval_minutes=interval_minutes,
        )
        if persisted_bars:
            return persisted_bars

        if not allow_tick_fallback:
            return []

        day_start_utc, day_end_utc = trade_day_window
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
                    "quality": "tick_aggregated",
                    "provider": source,
                    "synthetic": True,
                    "filledBy": "backend-tick-fallback",
                }
                bars.append(current_bar)
            elif current_bar is not None:
                current_bar["high"] = max(current_bar["high"], price)
                current_bar["low"] = min(current_bar["low"], price)
                current_bar["close"] = price
                current_bar["volume"] += volume_delta
                current_bar["amount"] += amount_delta
                current_bar["source"] = source
                current_bar["quality"] = "tick_aggregated"
                current_bar["provider"] = source
                current_bar["synthetic"] = True

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
            SELECT bucket_ts, source, raw
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
            "quality": self._intraday_quality_from_rows(rows),
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
            SELECT bucket_ts, open, high, low, close, volume, amount, source, raw
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
            raw_payload = _decode_jsonish(row["raw"]) or {}

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
                    "quality": raw_payload.get("quality") or "vendor_verified",
                    "synthetic": bool(raw_payload.get("synthetic", False)),
                    "provider": raw_payload.get("provider") or source,
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
            current_bar["quality"] = self._merge_intraday_quality(str(current_bar.get("quality") or "vendor_verified"), str(raw_payload.get("quality") or "vendor_verified"))
            current_bar["synthetic"] = bool(current_bar.get("synthetic")) or bool(raw_payload.get("synthetic", False))

        return bars

    @staticmethod
    def _merge_intraday_quality(left: str, right: str) -> str:
        priority = {
            "vendor_verified": 0,
            "realtime_aggregated": 1,
            "tick_aggregated": 2,
            "interpolated": 3,
            "stale": 4,
        }
        return left if priority.get(left, 99) >= priority.get(right, 99) else right

    @staticmethod
    def _intraday_quality_from_rows(rows: list[asyncpg.Record]) -> dict[str, object] | None:
        if not rows:
            return None
        quality_counts: dict[str, int] = {}
        synthetic_count = 0
        providers: set[str] = set()
        for row in rows:
            raw_payload = _decode_jsonish(row["raw"]) or {}
            quality = str(raw_payload.get("quality") or "vendor_verified")
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
            if raw_payload.get("synthetic"):
                synthetic_count += 1
            provider = raw_payload.get("provider") or row["source"]
            if provider is not None:
                providers.add(str(provider))
        dominant_quality = max(quality_counts.items(), key=lambda item: item[1])[0]
        return {
            "dominantQuality": dominant_quality,
            "qualityCounts": quality_counts,
            "syntheticCount": synthetic_count,
            "providers": sorted(providers),
        }

    @classmethod
    def _order_book_from_raw(cls, raw: dict[str, object]) -> dict[str, object]:
        quote_value = raw.get("quote")
        quote = {str(key): value for key, value in quote_value.items()} if isinstance(quote_value, dict) else {}
        bids = cls._order_book_levels(raw, quote, side="bid")
        asks = cls._order_book_levels(raw, quote, side="ask")
        best_bid = bids[0] if bids else {}
        best_ask = asks[0] if asks else {}
        return {
            "bid1": best_bid.get("price"),
            "bidVolume1": best_bid.get("volume"),
            "ask1": best_ask.get("price"),
            "askVolume1": best_ask.get("volume"),
            "bids": bids,
            "asks": asks,
            "source": raw.get("provider") or raw.get("source"),
        }

    @staticmethod
    def _order_book_levels(raw: dict[str, object], quote: dict[str, object], *, side: str) -> list[dict[str, object]]:
        levels: list[dict[str, object]] = []
        for level in range(1, 6):
            price = _raw_float(raw.get(f"{side}{level}"))
            if price is None:
                price = _raw_float(quote.get(f"{side}{level}"))

            volume = _raw_int(raw.get(f"{side}Volume{level}"))
            if volume is None:
                raw_lot_volume = _raw_int(quote.get(f"{side}_vol{level}"))
                volume = raw_lot_volume * 100 if raw_lot_volume is not None else None

            if price is None and volume is None:
                continue
            levels.append({"level": level, "price": price, "volume": volume})
        return levels

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
    def _daily_anomaly_ai_fields(
        *,
        reason: str | None = None,
        status: str | None = None,
        generated_at: object = None,
        related_news_ids: list[object] | None = None,
        related_announcement_ids: list[object] | None = None,
        phase: str | None = None,
        evidence_cutoff_at: object = None,
        includes_dragon_tiger: bool = False,
        post_close_required: bool = False,
        post_close_status: str | None = None,
        post_close_generated_at: object = None,
        post_close_reason: str | None = None,
    ) -> dict[str, object]:
        generated_at_iso = _to_iso(generated_at)
        post_close_generated_at_iso = _to_iso(post_close_generated_at)
        normalized_related_news_ids = list(related_news_ids or [])
        normalized_related_announcement_ids = list(related_announcement_ids or [])
        return {
            "aiReason": reason,
            "aiReasonStatus": status,
            "aiReasonGeneratedAt": generated_at_iso,
            "aiReasonPhase": phase or "intraday",
            "aiReasonEvidenceCutoffAt": _to_iso(evidence_cutoff_at),
            "aiReasonIncludesDragonTiger": bool(includes_dragon_tiger),
            "aiReasonPostCloseRequired": bool(post_close_required),
            "aiReasonPostCloseStatus": post_close_status or "not_due",
            "aiReasonPostCloseGeneratedAt": post_close_generated_at_iso,
            "aiReasonPostClose": post_close_reason,
            "relatedNewsIds": normalized_related_news_ids,
            "relatedAnnouncementIds": normalized_related_announcement_ids,
            "aiAttribution": reason,
            "aiAttributionStatus": status,
            "aiAttributionGeneratedAt": generated_at_iso,
        }

    @staticmethod
    def _build_daily_anomaly_candidates(
        *,
        symbols: list[str],
        rows: list[asyncpg.Record],
        snapshots: dict[str, dict[str, object]],
        average_volumes: dict[str, float],
        latest_volumes: dict[str, int],
        related_funds: dict[str, list[dict[str, object]]],
        target_day: date,
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
            snapshot_is_current = _snapshot_matches_trade_day(snapshot_payload, target_day)
            snapshot_change_pct: float | None = _to_float(snapshot_payload.get("changePct")) if snapshot_is_current else None
            snapshot_latest_price = _to_float(snapshot_payload.get("lastPrice")) if snapshot_is_current else None
            latest_volume = latest_volumes.get(symbol)
            stock_name = str(snapshot_payload.get("companyName") or symbol)

            for row in symbol_rows:
                row_ts = row["ts"]
                if not _is_market_session_ts(row_ts):
                    continue
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
                if snapshot_change_pct is None and _snapshot_matches_trade_day(event_snapshot_payload, target_day):
                    snapshot_change_pct = _to_float(event_snapshot_payload.get("changePct"))
                if snapshot_change_pct is None and isinstance(event_snapshot, dict) and _snapshot_matches_trade_day(event_snapshot, target_day):
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

            if latest_event_ts is None:
                continue

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
                    **MarketDetailQueryService._daily_anomaly_ai_fields(
                        status="pending",
                        phase="intraday",
                        includes_dragon_tiger=False,
                        post_close_required=severity in {"critical", "high"},
                        post_close_status="not_due",
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
    def _daily_anomaly_rank(item: dict[str, object]) -> tuple[int, float, str]:
        magnitude = max(
            abs(item["changePct"]) if isinstance(item.get("changePct"), float) else 0,
            abs(item["latestPriceJumpPct"]) if isinstance(item.get("latestPriceJumpPct"), float) else 0,
            item["volumeRatio"] if isinstance(item.get("volumeRatio"), float) else 0,
        )
        return (
            ANOMALY_SEVERITY_PRIORITY.get(str(item.get("severity")), 0),
            magnitude,
            str(item.get("triggerTime") or ""),
        )

    @staticmethod
    def _merge_related_funds(items: list[dict[str, object]]) -> list[dict[str, object]]:
        merged_funds: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            related_funds = item.get("relatedFunds")
            if not isinstance(related_funds, list):
                continue
            for fund in related_funds:
                if not isinstance(fund, dict):
                    continue
                fund_key = (str(fund.get("fundCode") or ""), str(fund.get("reportDate") or ""))
                if fund_key in seen:
                    continue
                seen.add(fund_key)
                merged_funds.append(fund)
        return merged_funds

    @staticmethod
    def _dedupe_daily_anomalies_by_symbol(items: list[dict[str, object]]) -> list[dict[str, object]]:
        grouped_items: dict[str, list[dict[str, object]]] = {}
        unkeyed_items: list[dict[str, object]] = []

        for item in items:
            symbol = item.get("symbol")
            if isinstance(symbol, str) and symbol:
                grouped_items.setdefault(symbol, []).append(item)
            else:
                unkeyed_items.append(item)

        deduped_items: list[dict[str, object]] = list(unkeyed_items)
        for symbol_items in grouped_items.values():
            representative = max(symbol_items, key=MarketDetailQueryService._daily_anomaly_rank)
            ai_representative = max(symbol_items, key=MarketDetailQueryService._daily_anomaly_ai_rank)
            event_count = sum(
                value
                for value in (_to_int(item.get("eventCountToday")) for item in symbol_items)
                if value is not None
            )
            deduped_items.append(
                {
                    **representative,
                    **MarketDetailQueryService._daily_anomaly_ai_snapshot(ai_representative),
                    "eventCountToday": event_count,
                    "relatedFunds": MarketDetailQueryService._merge_related_funds(symbol_items),
                    "intradayTimeline": MarketDetailQueryService._build_intraday_anomaly_timeline(symbol_items),
                }
            )

        return deduped_items

    @staticmethod
    def _daily_anomaly_ai_rank(item: dict[str, object]) -> tuple[int, int, str]:
        status_priority = {"completed": 3, "failed": 2, "skipped": 1, "pending": 0}
        phase_priority = {"post_close": 2, "reviewed": 1, "intraday": 0}
        status = str(item.get("aiReasonStatus") or "pending")
        phase = str(item.get("aiReasonPhase") or "intraday")
        generated_at = str(item.get("aiReasonGeneratedAt") or item.get("aiReasonPostCloseGeneratedAt") or "")
        return (
            status_priority.get(status, 0),
            phase_priority.get(phase, 0),
            generated_at,
        )

    @staticmethod
    def _daily_anomaly_ai_snapshot(item: dict[str, object]) -> dict[str, object]:
        return {
            "aiReason": item.get("aiReason"),
            "aiReasonStatus": item.get("aiReasonStatus"),
            "aiReasonGeneratedAt": item.get("aiReasonGeneratedAt"),
            "aiReasonPhase": item.get("aiReasonPhase"),
            "aiReasonEvidenceCutoffAt": item.get("aiReasonEvidenceCutoffAt"),
            "aiReasonIncludesDragonTiger": item.get("aiReasonIncludesDragonTiger"),
            "aiReasonPostCloseRequired": item.get("aiReasonPostCloseRequired"),
            "aiReasonPostCloseStatus": item.get("aiReasonPostCloseStatus"),
            "aiReasonPostCloseGeneratedAt": item.get("aiReasonPostCloseGeneratedAt"),
            "aiReasonPostClose": item.get("aiReasonPostClose"),
            "relatedNewsIds": item.get("relatedNewsIds"),
            "relatedAnnouncementIds": item.get("relatedAnnouncementIds"),
            "aiAttribution": item.get("aiAttribution"),
            "aiAttributionStatus": item.get("aiAttributionStatus"),
            "aiAttributionGeneratedAt": item.get("aiAttributionGeneratedAt"),
        }

    @staticmethod
    def _build_intraday_anomaly_timeline(items: list[dict[str, object]]) -> list[dict[str, object]]:
        timeline_by_bucket: dict[tuple[str, str], dict[str, object]] = {}
        for item in items:
            time_bucket = item.get("firstTriggerBucket") or item.get("triggerTime")
            trigger_time = item.get("triggerTime")
            session_segment = MarketDetailQueryService._session_segment_for_ts(trigger_time)
            if session_segment == "off_session":
                continue
            anomaly_type = str(item.get("anomalyType") or "unknown")
            bucket_key = str(time_bucket or item.get("triggerTime") or "")
            if not bucket_key:
                continue
            candidate = {
                "triggerTime": trigger_time,
                "timeBucket": time_bucket,
                "displayTime": MarketDetailQueryService._timeline_display_time(trigger_time),
                "sessionSegment": session_segment,
                "anomalyType": item.get("anomalyType"),
                "severity": item.get("severity"),
                "changePct": item.get("changePct"),
                "volumeRatio": item.get("volumeRatio"),
                "eventCountToday": _to_int(item.get("eventCountToday")) or 0,
                "aiReasonStatus": item.get("aiReasonStatus"),
                "aiReasonPhase": item.get("aiReasonPhase") or "intraday",
            }
            key = (bucket_key, anomaly_type)
            current = timeline_by_bucket.get(key)
            if current is None or MarketDetailQueryService._daily_anomaly_rank(candidate) > MarketDetailQueryService._daily_anomaly_rank(current):
                timeline_by_bucket[key] = candidate
        return sorted(timeline_by_bucket.values(), key=lambda entry: str(entry.get("timeBucket") or entry.get("triggerTime") or ""))

    @staticmethod
    def _timeline_display_time(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            local_ts = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(CHINA_MARKET_TZ)
        except ValueError:
            return None
        return local_ts.strftime("%H:%M")

    @staticmethod
    def _session_segment_for_ts(value: object) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            local_ts = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(CHINA_MARKET_TZ)
        except ValueError:
            return None
        minutes = local_ts.hour * 60 + local_ts.minute
        if 9 * 60 + 30 <= minutes < 10 * 60:
            return "open"
        if 10 * 60 <= minutes < 11 * 60 + 30:
            return "morning"
        if 11 * 60 + 30 <= minutes < 13 * 60:
            return "midday"
        if 13 * 60 <= minutes < 14 * 60 + 30:
            return "afternoon"
        if 14 * 60 + 30 <= minutes <= 15 * 60:
            return "close"
        return "off_session"

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
