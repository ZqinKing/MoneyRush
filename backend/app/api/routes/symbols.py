from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Request, status

from app.services.normalize.market_payloads import normalize_symbol_input


router = APIRouter(prefix="/symbols", tags=["symbols"])


class ActivateSymbolRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)


@router.get("/active")
async def active_symbols(request: Request) -> dict[str, list[str]]:
    redis_store = request.app.state.redis_store
    return {"symbols": await redis_store.get_active_symbols()}


@router.get("/snapshots")
async def active_snapshots(request: Request) -> dict[str, dict[str, object]]:
    redis_store = request.app.state.redis_store
    symbols = await redis_store.get_active_symbols()
    return {"snapshots": await redis_store.get_symbol_snapshots(symbols)}


@router.post("/activate", status_code=status.HTTP_202_ACCEPTED)
async def activate_symbol(payload: ActivateSymbolRequest, request: Request) -> dict[str, str]:
    try:
        symbol = normalize_symbol_input(payload.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    redis_store = request.app.state.redis_store
    await redis_store.activate_symbol(symbol)

    return {
        "status": "accepted",
        "symbol": symbol,
        "message": f"collector activation queued for {symbol}",
    }
