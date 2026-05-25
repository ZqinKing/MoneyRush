from __future__ import annotations

import hashlib
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.services.vendors.dragon_tiger_client import DragonTigerClientError


router = APIRouter(prefix="/dragon-tiger", tags=["dragon-tiger"])


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


@router.get("/daily")
async def dragon_tiger_daily(request: Request, date: str | None = Query(default=None)) -> dict[str, object]:
    trade_date = _validate_trade_date(date)
    redis_store = request.app.state.redis_store
    client = request.app.state.dragon_tiger_client
    settings = request.app.state.settings

    cache_key = _cache_key("daily", trade_date)
    cached = await redis_store.get_dragon_tiger_cache(cache_key)
    if cached is not None:
        return cached

    try:
        payload = client.fetch_daily(trade_date=trade_date)
    except DragonTigerClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"dragon tiger daily upstream failed: {exc}") from exc
    await redis_store.set_dragon_tiger_cache(cache_key, payload, settings.dragon_tiger_cache_ttl_seconds)
    return payload


@router.get("/stocks")
async def dragon_tiger_stocks(request: Request, range: str = Query(default="1month")) -> dict[str, object]:
    period = _validate_period(range)
    redis_store = request.app.state.redis_store
    client = request.app.state.dragon_tiger_client
    settings = request.app.state.settings

    cache_key = _cache_key("stocks", period)
    cached = await redis_store.get_dragon_tiger_cache(cache_key)
    if cached is not None:
        return cached

    try:
        payload = client.fetch_stock_stats(period=period)
    except DragonTigerClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"dragon tiger stock stats upstream failed: {exc}") from exc
    await redis_store.set_dragon_tiger_cache(cache_key, payload, settings.dragon_tiger_cache_ttl_seconds)
    return payload


@router.get("/institution")
async def dragon_tiger_institution(
    request: Request,
    startDate: str | None = Query(default=None),
    endDate: str | None = Query(default=None),
) -> dict[str, object]:
    start_date = _validate_trade_date(startDate)
    end_date = _validate_trade_date(endDate)
    redis_store = request.app.state.redis_store
    client = request.app.state.dragon_tiger_client
    settings = request.app.state.settings

    cache_key = _cache_key("institution", start_date, end_date)
    cached = await redis_store.get_dragon_tiger_cache(cache_key)
    if cached is not None:
        return cached

    try:
        payload = client.fetch_institution_trade_details(start_date=start_date, end_date=end_date)
    except DragonTigerClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"dragon tiger institution upstream failed: {exc}") from exc
    await redis_store.set_dragon_tiger_cache(cache_key, payload, settings.dragon_tiger_cache_ttl_seconds)
    return payload


@router.get("/branch-rank")
async def dragon_tiger_branch_rank(request: Request, range: str = Query(default="1month")) -> dict[str, object]:
    period = _validate_period(range)
    redis_store = request.app.state.redis_store
    client = request.app.state.dragon_tiger_client
    settings = request.app.state.settings

    cache_key = _cache_key("branch-rank", period)
    cached = await redis_store.get_dragon_tiger_cache(cache_key)
    if cached is not None:
        return cached

    try:
        payload = client.fetch_branch_rank(period=period)
    except DragonTigerClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"dragon tiger branch rank upstream failed: {exc}") from exc
    await redis_store.set_dragon_tiger_cache(cache_key, payload, settings.dragon_tiger_cache_ttl_seconds)
    return payload


@router.get("/stock/{symbol}/seat-dates")
async def dragon_tiger_stock_seat_dates(symbol: str, request: Request) -> dict[str, object]:
    normalized_symbol = _validate_symbol(symbol)
    redis_store = request.app.state.redis_store
    client = request.app.state.dragon_tiger_client
    settings = request.app.state.settings

    cache_key = _cache_key("seat-dates", normalized_symbol)
    cached = await redis_store.get_dragon_tiger_cache(cache_key)
    if cached is not None:
        return cached

    try:
        payload = client.fetch_stock_seat_detail_dates(symbol=normalized_symbol)
    except DragonTigerClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"dragon tiger seat date upstream failed: {exc}") from exc
    await redis_store.set_dragon_tiger_cache(cache_key, payload, settings.dragon_tiger_cache_ttl_seconds)
    return payload


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
    redis_store = request.app.state.redis_store
    client = request.app.state.dragon_tiger_client
    settings = request.app.state.settings

    compact_trade_date = trade_date.replace("-", "")
    cache_key = _cache_key("seat-detail", normalized_symbol, compact_trade_date, normalized_side)
    cached = await redis_store.get_dragon_tiger_cache(cache_key)
    if cached is not None:
        return cached

    try:
        payload = client.fetch_stock_seat_detail(symbol=normalized_symbol, trade_date=compact_trade_date, side=normalized_side)
    except DragonTigerClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"dragon tiger seat detail upstream failed: {exc}") from exc
    await redis_store.set_dragon_tiger_cache(cache_key, payload, settings.dragon_tiger_cache_ttl_seconds)
    return payload
