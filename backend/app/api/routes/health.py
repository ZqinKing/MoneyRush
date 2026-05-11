from __future__ import annotations

from http import HTTPStatus

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    settings = get_settings()
    redis_store = request.app.state.redis_store

    redis_status = "down"
    postgres_status = "down"

    try:
        if await redis_store.ping():
            redis_status = "up"
    except Exception:
        redis_status = "down"

    try:
        connection = await asyncpg.connect(settings.postgres_dsn)
        try:
            await connection.execute("SELECT 1")
            postgres_status = "up"
        finally:
            await connection.close()
    except Exception:
        postgres_status = "down"

    status_code = HTTPStatus.OK if redis_status == "up" and postgres_status == "up" else HTTPStatus.SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if status_code == HTTPStatus.OK else "degraded",
            "services": {
                "redis": redis_status,
                "postgres": postgres_status,
            },
        },
    )
