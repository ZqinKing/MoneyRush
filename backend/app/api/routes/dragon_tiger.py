from __future__ import annotations

import hashlib
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.services.vendors.dragon_tiger_client import DragonTigerClientError


router = APIRouter(prefix="/dragon-tiger", tags=["dragon-tiger"])


def _annotate_payload(payload: dict[str, object], *, stale: bool, detail: str | None = None) -> dict[str, object]:
    annotated = dict(payload)
    annotated["stale"] = stale
    annotated["sourceStatus"] = "stale-cache" if stale else "live"
    if detail:
        annotated["staleReason"] = detail
    return annotated


async def _resolve_payload(request: Request, *, cache_key: str, fetcher, error_prefix: str) -> dict[str, object]:
    redis_store = request.app.state.redis_store
    settings = request.app.state.settings
    cached = await redis_store.get_dragon_tiger_cache(cache_key)
    if cached is not None:
        return _annotate_payload(cached, stale=False)

    try:
        payload = fetcher()
    except DragonTigerClientError as exc:
        stale_cached = await redis_store.get_dragon_tiger_stale_cache(cache_key)
        if stale_cached is not None:
            return _annotate_payload(stale_cached, stale=True, detail=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{error_prefix}: {exc}") from exc

    await redis_store.set_dragon_tiger_cache(cache_key, payload, settings.dragon_tiger_cache_ttl_seconds)
    await redis_store.set_dragon_tiger_stale_cache(cache_key, payload, settings.dragon_tiger_stale_cache_ttl_seconds)
    return _annotate_payload(payload, stale=False)


def _cache_key(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_period(value: str) -> str:
    if value not in {"1month", "3month", "6month", "1year"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="range must be 1month, 3month, 6month, or 1year")
    return value


def _validate_trade_date(value: str | None) -> str:
    if value is None:
        return date.today().isoformat()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date must be YYYY-MM-DD") from exc


def _validate_required_trade_date(value: str | None, *, field_name: str) -> str:
    if value is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} is required")
    return _validate_trade_date(value)


def _validate_side(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"buy", "sell"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="side must be buy or sell")
    return normalized


def _validate_symbol(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="symbol is required")
    return normalized


def _validate_limit(value: int) -> int:
    if value < 1 or value > 200:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="limit must be between 1 and 200")
    return value


def _validate_optional_symbol(value: str | None) -> str | None:
    if value is None:
        return None
    return _validate_symbol(value)


def _validate_optional_trade_date(value: str | None, *, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} must be YYYY-MM-DD") from exc


@router.get("/daily")
async def dragon_tiger_daily(request: Request, date: str | None = Query(default=None)) -> dict[str, object]:
    trade_date = _validate_trade_date(date)
    client = request.app.state.dragon_tiger_client

    cache_key = _cache_key("daily", trade_date)
    return await _resolve_payload(
        request,
        cache_key=cache_key,
        fetcher=lambda: client.fetch_daily(trade_date=trade_date),
        error_prefix="dragon tiger daily upstream failed",
    )


@router.get("/stocks")
async def dragon_tiger_stocks(request: Request, range: str = Query(default="1month")) -> dict[str, object]:
    period = _validate_period(range)
    client = request.app.state.dragon_tiger_client

    cache_key = _cache_key("stocks", period)
    return await _resolve_payload(
        request,
        cache_key=cache_key,
        fetcher=lambda: client.fetch_stock_stats(period=period),
        error_prefix="dragon tiger stock stats upstream failed",
    )


@router.get("/institution")
async def dragon_tiger_institution(
    request: Request,
    startDate: str | None = Query(default=None),
    endDate: str | None = Query(default=None),
) -> dict[str, object]:
    start_date = _validate_trade_date(startDate)
    end_date = _validate_trade_date(endDate)
    client = request.app.state.dragon_tiger_client

    cache_key = _cache_key("institution", start_date, end_date)
    return await _resolve_payload(
        request,
        cache_key=cache_key,
        fetcher=lambda: client.fetch_institution_trade_details(start_date=start_date, end_date=end_date),
        error_prefix="dragon tiger institution upstream failed",
    )


@router.get("/branch-rank")
async def dragon_tiger_branch_rank(request: Request, range: str = Query(default="1month")) -> dict[str, object]:
    period = _validate_period(range)
    client = request.app.state.dragon_tiger_client

    cache_key = _cache_key("branch-rank", period)
    return await _resolve_payload(
        request,
        cache_key=cache_key,
        fetcher=lambda: client.fetch_branch_rank(period=period),
        error_prefix="dragon tiger branch rank upstream failed",
    )


@router.get("/stock/{symbol}/seat-dates")
async def dragon_tiger_stock_seat_dates(symbol: str, request: Request) -> dict[str, object]:
    normalized_symbol = _validate_symbol(symbol)
    client = request.app.state.dragon_tiger_client

    cache_key = _cache_key("seat-dates", normalized_symbol)
    return await _resolve_payload(
        request,
        cache_key=cache_key,
        fetcher=lambda: client.fetch_stock_seat_detail_dates(symbol=normalized_symbol),
        error_prefix="dragon tiger seat date upstream failed",
    )


@router.get("/stock/{symbol}/seat-detail")
async def dragon_tiger_stock_seat_detail(
    symbol: str,
    request: Request,
    date: str | None = Query(default=None),
    side: str = Query(default="buy"),
) -> dict[str, object]:
    normalized_symbol = _validate_symbol(symbol)
    trade_date = _validate_required_trade_date(date, field_name="date")
    normalized_side = _validate_side(side)
    client = request.app.state.dragon_tiger_client

    compact_trade_date = trade_date.replace("-", "")
    cache_key = _cache_key("seat-detail", normalized_symbol, compact_trade_date, normalized_side)
    return await _resolve_payload(
        request,
        cache_key=cache_key,
        fetcher=lambda: client.fetch_stock_seat_detail(symbol=normalized_symbol, trade_date=compact_trade_date, side=normalized_side),
        error_prefix="dragon tiger seat detail upstream failed",
    )


@router.get("/history/daily")
async def dragon_tiger_daily_history(
    request: Request,
    symbol: str | None = Query(default=None),
    startDate: str | None = Query(default=None),
    endDate: str | None = Query(default=None),
    limit: int = Query(default=60),
) -> dict[str, object]:
    normalized_symbol = _validate_optional_symbol(symbol)
    start_date = _validate_optional_trade_date(startDate, field_name="startDate")
    end_date = _validate_optional_trade_date(endDate, field_name="endDate")
    validated_limit = _validate_limit(limit)
    query_service = request.app.state.dragon_tiger_query_service
    items = await query_service.fetch_daily_history(
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
        limit=validated_limit,
    )
    return {
        "items": items,
        "filters": {
            "symbol": normalized_symbol,
            "startDate": start_date.isoformat() if start_date else None,
            "endDate": end_date.isoformat() if end_date else None,
            "limit": validated_limit,
        },
    }


@router.get("/history/institution")
async def dragon_tiger_institution_history(
    request: Request,
    symbol: str | None = Query(default=None),
    startDate: str | None = Query(default=None),
    endDate: str | None = Query(default=None),
    limit: int = Query(default=60),
) -> dict[str, object]:
    normalized_symbol = _validate_optional_symbol(symbol)
    start_date = _validate_optional_trade_date(startDate, field_name="startDate")
    end_date = _validate_optional_trade_date(endDate, field_name="endDate")
    validated_limit = _validate_limit(limit)
    query_service = request.app.state.dragon_tiger_query_service
    items = await query_service.fetch_institution_history(
        symbol=normalized_symbol,
        start_date=start_date,
        end_date=end_date,
        limit=validated_limit,
    )
    return {
        "items": items,
        "filters": {
            "symbol": normalized_symbol,
            "startDate": start_date.isoformat() if start_date else None,
            "endDate": end_date.isoformat() if end_date else None,
            "limit": validated_limit,
        },
    }


@router.get("/history/summary")
async def dragon_tiger_history_summary(request: Request) -> dict[str, object]:
    query_service = request.app.state.dragon_tiger_query_service
    return await query_service.fetch_history_summary()
