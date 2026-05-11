from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.services.streaming.broadcaster import MarketBroadcaster


router = APIRouter(tags=["market-ws"])


@router.websocket("/ws/market")
async def market_stream(websocket: WebSocket) -> None:
    settings = get_settings()
    redis_store = websocket.app.state.redis_store
    broadcaster = MarketBroadcaster(redis_store)

    await websocket.accept()

    try:
        while True:
            await websocket.send_json(await broadcaster.build_heartbeat_event())
            await asyncio.sleep(settings.ws_heartbeat_interval_seconds)
    except WebSocketDisconnect:
        return
    except RuntimeError:
        return
