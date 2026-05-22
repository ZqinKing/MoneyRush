from __future__ import annotations

from fastapi import APIRouter, Request


router = APIRouter(prefix="/market", tags=["market"])


@router.get("/overview")
async def market_overview(request: Request) -> dict[str, object]:
    redis_store = request.app.state.redis_store
    payload = await redis_store.get_market_overview()
    if payload is not None:
        return {
            "generatedAt": payload.get("generatedAt"),
            "marketStatus": payload.get("marketStatus", "closed"),
            "isTradingSession": payload.get("isTradingSession", False),
            "serverGeneratedAt": payload.get("serverGeneratedAt"),
            "indexes": payload.get("indexes", []),
            "breadth": payload.get("breadth"),
        }

    return {
        "generatedAt": None,
        "marketStatus": "closed",
        "isTradingSession": False,
        "serverGeneratedAt": None,
        "indexes": [],
        "breadth": None,
    }
