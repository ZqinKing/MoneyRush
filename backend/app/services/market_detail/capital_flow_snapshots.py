from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Protocol


CHINA_MARKET_TZ = timezone(timedelta(hours=8))
CAPITAL_FLOW_READY_TIME_CHINA = time(hour=17, minute=10)
CAPITAL_FLOW_STALE_REASON = "资金流向数据尚未更新至当前交易日。"
CAPITAL_FLOW_REFERENCE_MISSING_REASON = "资金流向参考交易日不可用。"

CAPITAL_FLOW_SNAPSHOT_KEYS = (
    "capitalFlowMainNetInflow",
    "capitalFlowMainNetRatio",
    "capitalFlowTradeDate",
    "capitalFlowReferenceTradeDate",
    "capitalFlowSourceStatus",
    "capitalFlowStale",
    "capitalFlowStaleReason",
)


class CapitalFlowQueryService(Protocol):
    async def fetch_latest_capital_flows(self, symbols: list[str]) -> dict[str, dict[str, object]]: ...


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _is_weekend(value: date) -> bool:
    return value.weekday() >= 5


def _previous_trade_date(value: date) -> date:
    candidate = value - timedelta(days=1)
    while _is_weekend(candidate):
        candidate -= timedelta(days=1)
    return candidate


def expected_capital_flow_trade_date(value: object) -> str | None:
    timestamp = _parse_iso_datetime(value)
    if timestamp is None:
        return None
    local_timestamp = timestamp.astimezone(CHINA_MARKET_TZ)
    local_date = local_timestamp.date()
    if _is_weekend(local_date) or local_timestamp.time() < CAPITAL_FLOW_READY_TIME_CHINA:
        return _previous_trade_date(local_date).isoformat()
    return local_date.isoformat()


def _clear_snapshot_capital_flow(snapshot: dict[str, object]) -> None:
    for key in CAPITAL_FLOW_SNAPSHOT_KEYS:
        _ = snapshot.pop(key, None)


def _number_or_none(value: object) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return value
    return None


async def enrich_snapshots_with_capital_flow(
    *,
    snapshots: dict[str, dict[str, object]],
    symbols: list[str],
    query_service: CapitalFlowQueryService,
) -> dict[str, dict[str, object]]:
    capital_flows = await query_service.fetch_latest_capital_flows(symbols)

    for symbol in symbols:
        snapshot = snapshots.get(symbol)
        if not isinstance(snapshot, dict):
            continue

        _clear_snapshot_capital_flow(snapshot)
        capital_flow = capital_flows.get(symbol)
        if not capital_flow:
            continue

        reference_trade_date = expected_capital_flow_trade_date(snapshot.get("updatedAt"))
        capital_flow_trade_date = capital_flow.get("tradeDate")
        snapshot["capitalFlowTradeDate"] = capital_flow_trade_date

        if reference_trade_date is None:
            snapshot["capitalFlowSourceStatus"] = "stale"
            snapshot["capitalFlowStale"] = True
            snapshot["capitalFlowStaleReason"] = CAPITAL_FLOW_REFERENCE_MISSING_REASON
            continue

        snapshot["capitalFlowReferenceTradeDate"] = reference_trade_date
        if capital_flow_trade_date != reference_trade_date:
            snapshot["capitalFlowSourceStatus"] = "stale"
            snapshot["capitalFlowStale"] = True
            snapshot["capitalFlowStaleReason"] = CAPITAL_FLOW_STALE_REASON
            continue

        snapshot["capitalFlowMainNetInflow"] = _number_or_none(capital_flow.get("mainNetInflow"))
        snapshot["capitalFlowMainNetRatio"] = _number_or_none(capital_flow.get("mainNetRatio"))
        snapshot["capitalFlowSourceStatus"] = capital_flow.get("sourceStatus")
        snapshot["capitalFlowStale"] = bool(capital_flow.get("stale"))
        stale_reason = capital_flow.get("staleReason")
        if stale_reason:
            snapshot["capitalFlowStaleReason"] = stale_reason

    return snapshots
