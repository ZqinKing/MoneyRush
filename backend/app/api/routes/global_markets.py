from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/global-markets", tags=["global-markets"])


@router.get("/latest")
async def global_markets_latest(request: Request) -> dict[str, object]:
    redis_store = request.app.state.redis_store
    payload = await redis_store.get_global_markets_latest()
    if payload is None:
        raise HTTPException(status_code=503, detail="global markets cache unavailable")

    return {
        "items": payload.get("items", []),
        "regions": payload.get("regions", []),
        "source": payload.get("source"),
        "updatedAt": payload.get("updatedAt"),
        "delayLabel": payload.get("delayLabel"),
        "stale": bool(payload.get("stale", False)),
        "errors": payload.get("errors", []),
    }
