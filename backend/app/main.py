from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
from app.api.routes.symbols import router as symbols_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.cache.redis_store import RedisStore
from app.ws.market import router as market_ws_router


configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.redis_store = RedisStore(
        redis_url=settings.redis_url,
        stream_key=settings.redis_stream_key,
        active_symbols_key=settings.active_symbols_key,
        market_snapshot_key_prefix=settings.market_snapshot_key_prefix,
        market_event_key_prefix=settings.market_event_key_prefix,
        market_events_stream_key=settings.market_events_stream_key,
    )

    yield

    await app.state.redis_store.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "environment": settings.app_env,
            "status": "bootstrapped",
        }

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(symbols_router, prefix="/api/v1")
    app.include_router(market_ws_router)
    return app


app = create_app()
