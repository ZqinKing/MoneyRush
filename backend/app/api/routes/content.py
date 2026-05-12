from __future__ import annotations

import hashlib
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.services.normalize.market_payloads import normalize_symbol_input


router = APIRouter(prefix="/content", tags=["content"])


def _normalize_optional_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    try:
        return normalize_symbol_input(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _validate_type(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in {"report", "news", "announcement"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="type must be report, news, or announcement")
    return value


def _validate_scope(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in {"symbol", "market"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="scope must be symbol or market")
    return value


def _validate_limit(value: int) -> int:
    if value < 1 or value > 100:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="limit must be between 1 and 100")
    return value


def _parse_before(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="before must be ISO-8601 datetime") from exc


def _cache_key(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.get("/items")
async def content_items(
    request: Request,
    symbol: str | None = None,
    type: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    limit: int = 20,
    before: str | None = None,
) -> dict[str, object]:
    normalized_symbol = _normalize_optional_symbol(symbol)
    normalized_type = _validate_type(type)
    normalized_scope = _validate_scope(scope)
    validated_limit = _validate_limit(limit)
    parsed_before = _parse_before(before)

    redis_store = request.app.state.redis_store
    query_service = request.app.state.content_query_service
    settings = request.app.state.settings

    cache_key = _cache_key("items", normalized_symbol, normalized_type, normalized_scope, validated_limit, before)
    cached = await redis_store.get_content_feed_cache(cache_key)
    if cached is not None:
        return cached

    items = await query_service.fetch_feed(
        symbol=normalized_symbol,
        content_type=normalized_type,
        scope=normalized_scope,
        limit=validated_limit,
        before=parsed_before,
    )
    payload = {
        "items": items,
        "filters": {
            "symbol": normalized_symbol,
            "type": normalized_type,
            "scope": normalized_scope,
            "limit": validated_limit,
            "before": before,
        },
    }
    await redis_store.set_content_feed_cache(cache_key, payload, settings.content_query_cache_ttl_seconds)
    return payload


@router.get("/status")
async def content_status(request: Request, symbol: str | None = None) -> dict[str, object]:
    normalized_symbol = _normalize_optional_symbol(symbol)
    redis_store = request.app.state.redis_store
    query_service = request.app.state.content_query_service
    settings = request.app.state.settings

    cache_key = _cache_key("status", normalized_symbol)
    cached = await redis_store.get_content_status_cache(cache_key)
    if cached is not None:
        return cached

    payload = await query_service.fetch_status(symbol=normalized_symbol)
    await redis_store.set_content_status_cache(cache_key, payload, settings.content_query_cache_ttl_seconds)
    return payload
