from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.content import router as content_router
from app.api.routes.health import router as health_router
from app.api.routes.market import router as market_router
from app.api.routes.symbols import router as symbols_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.cache.redis_store import RedisStore
from app.services.content_query_service import ContentQueryService
from app.services.market_detail.query_service import MarketDetailQueryService
from app.services.symbol_lookup import SymbolLookupService
from app.ws.market import router as market_ws_router


configure_logging()


def build_allowed_origins(frontend_origin: str) -> list[str]:
    origins = [frontend_origin]

    try:
        if frontend_origin.startswith("http://localhost:"):
            origins.append(frontend_origin.replace("http://localhost:", "http://127.0.0.1:", 1))
        elif frontend_origin.startswith("http://127.0.0.1:"):
            origins.append(frontend_origin.replace("http://127.0.0.1:", "http://localhost:", 1))
    except Exception:
        return origins

    return list(dict.fromkeys(origins))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.redis_store = RedisStore(
        redis_url=settings.redis_url,
        stream_key=settings.redis_stream_key,
        active_symbols_key=settings.active_symbols_key,
        market_snapshot_key_prefix=settings.market_snapshot_key_prefix,
        market_event_key_prefix=settings.market_event_key_prefix,
        market_events_stream_key=settings.market_events_stream_key,
        market_overview_cache_key=settings.market_overview_cache_key,
        content_feed_cache_key_prefix=settings.content_feed_cache_key_prefix,
        content_status_cache_key_prefix=settings.content_status_cache_key_prefix,
    )
    app.state.market_detail_query_service = MarketDetailQueryService(settings.postgres_dsn)
    app.state.content_query_service = ContentQueryService(
        settings.postgres_dsn,
        lane_refresh_seconds={
            "symbol-report": settings.content_report_refresh_seconds,
            "symbol-news": settings.content_news_refresh_seconds,
            "symbol-announcement": settings.content_announcement_refresh_seconds,
            "market-news": settings.content_market_news_refresh_seconds,
        },
    )
    app.state.symbol_lookup_service = SymbolLookupService()
    await app.state.market_detail_query_service.connect()
    await app.state.content_query_service.connect()

    yield

    await app.state.content_query_service.close()
    await app.state.market_detail_query_service.close()
    await app.state.redis_store.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=build_allowed_origins(settings.frontend_origin),
        allow_origin_regex=settings.frontend_origin_regex,
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
    app.include_router(market_router, prefix="/api/v1")
    app.include_router(symbols_router, prefix="/api/v1")
    app.include_router(content_router, prefix="/api/v1")
    app.include_router(market_ws_router)
    return app


app = create_app()
