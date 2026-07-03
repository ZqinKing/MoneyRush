from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.services.market_detail.capital_flow_snapshots import CAPITAL_FLOW_STALE_REASON, enrich_snapshots_with_capital_flow, expected_capital_flow_trade_date
from app.services.normalize.market_payloads import normalize_symbol_input


router = APIRouter(prefix="/symbols", tags=["symbols"])
CHINA_MARKET_TZ = timezone(timedelta(hours=8))


class ActivateSymbolRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)


def _normalize_symbol_or_422(symbol: str) -> str:
    try:
        return normalize_symbol_input(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _validate_limit(limit: int) -> int:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="limit must be between 1 and 200")
    return limit


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_same_china_trade_day(left: object, right: object) -> bool:
    left_ts = _parse_iso_datetime(left)
    right_ts = _parse_iso_datetime(right)
    if left_ts is None or right_ts is None:
        return False
    return left_ts.astimezone(CHINA_MARKET_TZ).date() == right_ts.astimezone(CHINA_MARKET_TZ).date()


def _filter_intraday_bars_for_reference_day(
    bars: list[dict[str, object]],
    *,
    reference_ts: object,
) -> list[dict[str, object]]:
    if not bars or reference_ts is None:
        return []
    return [bar for bar in bars if _is_same_china_trade_day(bar.get("bucketTs"), reference_ts)]


@router.get("/active")
async def active_symbols(request: Request) -> dict[str, list[str]]:
    redis_store = request.app.state.redis_store
    return {"symbols": await redis_store.get_active_symbols()}


@router.get("/snapshots")
async def active_snapshots(request: Request) -> dict[str, dict[str, object]]:
    redis_store = request.app.state.redis_store
    query_service = request.app.state.market_detail_query_service
    symbols = await redis_store.get_active_symbols()
    snapshots = await redis_store.get_symbol_snapshots(symbols)
    _ = await enrich_snapshots_with_capital_flow(snapshots=snapshots, symbols=symbols, query_service=query_service)

    return {"snapshots": snapshots}


@router.get("/event-summaries")
async def active_event_summaries(request: Request) -> dict[str, dict[str, object]]:
    redis_store = request.app.state.redis_store
    query_service = request.app.state.market_detail_query_service
    symbols = await redis_store.get_active_symbols()

    summaries: dict[str, dict[str, object]] = {}
    for symbol in symbols:
        summaries[symbol] = await query_service.fetch_event_summary(symbol)

    return {"summaries": summaries}


@router.get("/{symbol}/detail")
async def symbol_detail(symbol: str, request: Request) -> dict[str, object]:
    normalized_symbol = _normalize_symbol_or_422(symbol)
    redis_store = request.app.state.redis_store
    query_service = request.app.state.market_detail_query_service

    snapshot = await redis_store.get_symbol_snapshot(normalized_symbol)
    if snapshot is None:
        snapshot = await query_service.fetch_snapshot(normalized_symbol)

    latest_event = await redis_store.get_symbol_event(normalized_symbol)
    if latest_event is None:
        latest_event = await query_service.fetch_latest_event(normalized_symbol)

    latest_kline = await query_service.fetch_latest_kline(normalized_symbol, period="1d")
    capital_flow = await query_service.fetch_latest_capital_flow(normalized_symbol)
    daily_bars_preview = await query_service.fetch_klines(normalized_symbol, period="1d", limit=30)
    reference_intraday_ts = (
        (snapshot or {}).get("updatedAt")
        or (latest_event or {}).get("generatedAt")
        or (latest_kline or {}).get("bucketTs")
    )
    intraday_minute_bars = await query_service.fetch_intraday_sampled_bars(
        normalized_symbol,
        interval_minutes=1,
        allow_tick_fallback=False,
        reference_ts=reference_intraday_ts,
    )
    intraday_sampled_bars = await query_service.fetch_intraday_sampled_bars(
        normalized_symbol,
        interval_minutes=5,
        reference_ts=reference_intraday_ts,
    )
    order_book = await query_service.fetch_order_book(normalized_symbol)
    fund_holding_summary = await request.app.state.fund_query_service.fetch_stock_funds(normalized_symbol)

    capital_flow_reference_trade_day = expected_capital_flow_trade_date(
        reference_intraday_ts or (latest_kline or {}).get("bucketTs")
    )
    if (
        capital_flow
        and capital_flow.get("source") != "capital-flow-unavailable"
        and capital_flow_reference_trade_day
        and capital_flow.get("tradeDate") != capital_flow_reference_trade_day
    ):
        capital_flow["sourceStatus"] = "stale"
        capital_flow["stale"] = True
        capital_flow["staleReason"] = CAPITAL_FLOW_STALE_REASON

    intraday_minute_bars = _filter_intraday_bars_for_reference_day(
        intraday_minute_bars,
        reference_ts=reference_intraday_ts,
    )
    intraday_sampled_bars = _filter_intraday_bars_for_reference_day(
        intraday_sampled_bars,
        reference_ts=reference_intraday_ts,
    )
    intraday_completeness = await query_service.fetch_intraday_completeness(
        normalized_symbol,
        reference_ts=reference_intraday_ts,
        reconciliation_seconds=request.app.state.settings.collector_intraday_post_close_reconciliation_seconds,
    )

    return {
        "symbol": normalized_symbol,
        "snapshot": snapshot,
        "latestEvent": latest_event,
        "latestKline": latest_kline,
        "dailyBarsPreview": list(reversed(daily_bars_preview)),
        "intradayMinuteBars": intraday_minute_bars,
        "intradaySampledBars": intraday_sampled_bars,
        "intradayCompleteness": intraday_completeness,
        "orderBook": order_book,
        "fundHoldingSummary": fund_holding_summary,
        "capitalFlow": capital_flow,
        "capabilities": {
            "supportsIntradayKline": len(intraday_minute_bars) > 1,
            "supportsIntradayMinuteBars": len(intraday_minute_bars) > 1,
            "supportsOrderBookDepth5": _supports_order_book_depth5(order_book),
            "supportsSampledIntradayBars": len(intraday_sampled_bars) > 1,
            "supportsBestBidAsk": _supports_best_bid_ask(order_book),
            "supportsFundHoldings": True,
            "supportsCapitalFlow": capital_flow is not None,
        },
    }


def _supports_best_bid_ask(order_book: dict[str, object]) -> bool:
    return any(order_book.get(key) is not None for key in ("bid1", "bidVolume1", "ask1", "askVolume1"))


def _supports_order_book_depth5(order_book: dict[str, object]) -> bool:
    for side in ("bids", "asks"):
        levels = order_book.get(side)
        if not isinstance(levels, list):
            continue
        if any(isinstance(level, dict) and level.get("level") == 5 and (level.get("price") is not None or level.get("volume") is not None) for level in levels):
            return True
    return False


@router.get("/{symbol}/funds")
async def symbol_funds(symbol: str, request: Request) -> dict[str, object]:
    normalized_symbol = _normalize_symbol_or_422(symbol)
    return await request.app.state.fund_query_service.fetch_stock_funds(normalized_symbol)


@router.get("/{symbol}/ticks")
async def symbol_ticks(symbol: str, request: Request, limit: int = 60) -> dict[str, object]:
    normalized_symbol = _normalize_symbol_or_422(symbol)
    validated_limit = _validate_limit(limit)
    query_service = request.app.state.market_detail_query_service
    return {
        "symbol": normalized_symbol,
        "ticks": await query_service.fetch_ticks(normalized_symbol, validated_limit),
    }


@router.get("/{symbol}/kline")
async def symbol_kline(symbol: str, request: Request, period: str = "1d", limit: int = 1) -> dict[str, object]:
    normalized_symbol = _normalize_symbol_or_422(symbol)
    validated_limit = _validate_limit(limit)
    if period != "1d":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="only period=1d is currently supported")

    query_service = request.app.state.market_detail_query_service
    return {
        "symbol": normalized_symbol,
        "period": period,
        "klines": await query_service.fetch_klines(normalized_symbol, period=period, limit=validated_limit),
    }


@router.get("/{symbol}/events")
async def symbol_events(symbol: str, request: Request, limit: int = 20) -> dict[str, object]:
    normalized_symbol = _normalize_symbol_or_422(symbol)
    validated_limit = _validate_limit(limit)
    query_service = request.app.state.market_detail_query_service
    return {
        "symbol": normalized_symbol,
        "events": await query_service.fetch_events(normalized_symbol, validated_limit),
    }


@router.post("/activate")
async def activate_symbol(payload: ActivateSymbolRequest, request: Request) -> JSONResponse:
    symbol = _normalize_symbol_or_422(payload.symbol)

    redis_store = request.app.state.redis_store
    symbol_lookup_service = request.app.state.symbol_lookup_service

    if await redis_store.is_symbol_active(symbol):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "already_active",
                "symbol": symbol,
                "message": f"{symbol} 已在监控列表中",
            },
        )

    try:
        lookup_result = symbol_lookup_service.lookup(symbol)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"股票校验失败：{exc}") from exc

    if not lookup_result.is_valid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"股票代码 {symbol} 不存在")

    await redis_store.activate_symbol(symbol)
    await redis_store.clear_content_caches()

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "symbol": symbol,
            "companyName": lookup_result.company_name,
            "message": f"已将 {symbol} {lookup_result.company_name or ''} 加入监控队列".strip(),
        },
    )


@router.delete("/{symbol}", status_code=status.HTTP_202_ACCEPTED)
async def deactivate_symbol(symbol: str, request: Request) -> dict[str, str]:
    normalized_symbol = _normalize_symbol_or_422(symbol)

    redis_store = request.app.state.redis_store
    content_query_service = request.app.state.content_query_service
    await redis_store.deactivate_symbol(normalized_symbol)
    await content_query_service.delete_symbol_tracking(normalized_symbol)
    await redis_store.clear_content_caches()

    return {
        "status": "accepted",
        "symbol": normalized_symbol,
        "message": f"collector deactivation queued for {normalized_symbol}",
    }
