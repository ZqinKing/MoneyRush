from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal


BEIJING_TZ = timezone(timedelta(hours=8))
DISPLAY_TIMEZONE = "Asia/Shanghai"


def _coerce_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce_date(value: date | datetime | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def _slug(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    return text.strip("-") or "event"


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def build_duplicate_group_key(
    *,
    event_kind: str,
    title: str,
    event_time: datetime | None,
    event_date: date,
    end_date: date | None = None,
) -> str:
    if event_time is not None:
        scheduled_key = _coerce_utc_datetime(event_time).replace(second=0, microsecond=0).isoformat()
    elif end_date is not None and end_date != event_date:
        scheduled_key = f"{event_date.isoformat()}:{end_date.isoformat()}"
    else:
        scheduled_key = event_date.isoformat()
    return f"{_slug(event_kind)}:{_slug(title)}:{scheduled_key}"


def build_timeline_event_id(duplicate_group_key: str) -> str:
    digest = hashlib.sha1(duplicate_group_key.encode("utf-8")).hexdigest()[:24]
    return f"event-calendar-{digest}"


def normalize_timeline_event(
    *,
    title: str,
    category: str,
    level: str,
    event_kind: str,
    source: str,
    source_provider: str,
    impact_assets: list[str],
    event_time: datetime | None = None,
    event_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
    event_timezone: str | None = None,
    source_event_id: str | None = None,
    source_url: str | None = None,
    description: str | None = None,
    previous_value: str | None = None,
    market_expectation: str | None = None,
    importance_score: float | int | Decimal | None = None,
    confidence_score: float | int | Decimal | None = None,
    actual_value: float | int | Decimal | None = None,
    forecast_value: float | int | Decimal | None = None,
    previous_value_numeric: float | int | Decimal | None = None,
    unit: str | None = None,
    raw_payload: dict[str, object] | None = None,
) -> dict[str, object]:
    normalized_time = _coerce_utc_datetime(event_time) if event_time is not None else None
    normalized_event_date = _coerce_date(event_date)
    normalized_end_date = _coerce_date(end_date)
    if normalized_event_date is None:
        if normalized_time is None:
            raise ValueError("event_date is required when event_time is missing")
        normalized_event_date = normalized_time.astimezone(BEIJING_TZ).date()

    duplicate_group_key = build_duplicate_group_key(
        event_kind=event_kind,
        title=title,
        event_time=normalized_time,
        event_date=normalized_event_date,
        end_date=normalized_end_date,
    )
    event_id = build_timeline_event_id(duplicate_group_key)
    payload = _json_safe(raw_payload or {})
    return {
        "id": event_id,
        "event_date": normalized_event_date,
        "end_date": normalized_end_date,
        "title": title,
        "category": category,
        "impact_assets": impact_assets,
        "level": level,
        "source": source,
        "description": description,
        "previous_value": previous_value,
        "market_expectation": market_expectation,
        "status": "upcoming",
        "source_url": source_url,
        "display_timezone": DISPLAY_TIMEZONE,
        "event_time": normalized_time,
        "event_timezone": event_timezone,
        "source_provider": source_provider,
        "source_event_id": source_event_id,
        "event_kind": event_kind,
        "importance_score": _optional_float(importance_score),
        "confidence_score": _optional_float(confidence_score),
        "actual_value": _optional_float(actual_value),
        "forecast_value": _optional_float(forecast_value),
        "previous_value_numeric": _optional_float(previous_value_numeric),
        "unit": unit,
        "raw_payload": json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
        "duplicate_group_key": duplicate_group_key,
    }
