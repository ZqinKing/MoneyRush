from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.anomaly import router as anomaly_router
from app.api.routes.content import router as content_router
from app.api.routes.dragon_tiger import router as dragon_tiger_router
from app.api.routes.funds import router as funds_router
from app.api.routes.gold import router as gold_router
from app.api.routes.health import router as health_router
from app.api.routes.llm_audit import router as llm_audit_router
from app.api.routes.macro import router as macro_router
from app.api.routes.market import router as market_router
from app.api.routes.symbols import router as symbols_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.cache.redis_store import RedisStore
from app.services.content_query_service import ContentQueryService
from app.services.dragon_tiger_query_service import DragonTigerQueryService
from app.services.fund_lookup import FundLookupService
from app.services.fund_query_service import FundQueryService
from app.services.llm_audit_query_service import LlmAuditQueryService
from app.services.macro_analysis_service import MacroAnalysisService
from app.services.macro_query_service import MacroQueryService
from app.services.market_detail.query_service import MarketDetailQueryService
from app.services.symbol_lookup import SymbolLookupService
from app.services.vendors.dragon_tiger_client import DragonTigerClient
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
        gold_dashboard_cache_key=settings.gold_dashboard_cache_key,
        active_funds_key=settings.active_funds_key,
        fund_snapshot_key_prefix=settings.fund_snapshot_key_prefix,
        fund_holdings_key_prefix=settings.fund_holdings_key_prefix,
        fund_auto_link_stocks_key_prefix=settings.fund_auto_link_stocks_key_prefix,
        stock_funds_key_prefix=settings.stock_funds_key_prefix,
        content_feed_cache_key_prefix=settings.content_feed_cache_key_prefix,
        content_status_cache_key_prefix=settings.content_status_cache_key_prefix,
        dragon_tiger_cache_key_prefix=settings.dragon_tiger_cache_key_prefix,
        macro_snapshot_cache_key=settings.macro_snapshot_cache_key,
        macro_analysis_latest_cache_key=settings.macro_analysis_latest_cache_key,
        macro_collector_status_cache_key=settings.macro_collector_status_cache_key,
    )
    app.state.market_detail_query_service = MarketDetailQueryService(settings.postgres_dsn)
    app.state.fund_query_service = FundQueryService(settings.postgres_dsn)
    app.state.content_query_service = ContentQueryService(
        settings.postgres_dsn,
        lane_refresh_seconds={
            "symbol-report": settings.content_report_refresh_seconds,
            "symbol-news": settings.content_news_refresh_seconds,
            "symbol-announcement": settings.content_announcement_refresh_seconds,
            "market-news": settings.content_market_news_refresh_seconds,
        },
    )
    app.state.dragon_tiger_query_service = DragonTigerQueryService(settings.postgres_dsn)
    app.state.macro_query_service = MacroQueryService(settings.postgres_dsn)
    app.state.llm_audit_query_service = LlmAuditQueryService(settings.postgres_dsn)
    app.state.macro_analysis_service = MacroAnalysisService(settings)
    app.state.symbol_lookup_service = SymbolLookupService()
    app.state.fund_lookup_service = FundLookupService()
    app.state.dragon_tiger_client = DragonTigerClient(
        timeout_seconds=settings.dragon_tiger_request_timeout_seconds,
        retry_attempts=settings.dragon_tiger_request_retry_attempts,
        retry_backoff_seconds=settings.dragon_tiger_request_retry_backoff_seconds,
    )
    await app.state.market_detail_query_service.connect()
    await app.state.fund_query_service.connect()
    await app.state.content_query_service.connect()
    await app.state.dragon_tiger_query_service.connect()
    await app.state.macro_query_service.connect()
    await app.state.llm_audit_query_service.connect()

    yield

    await app.state.llm_audit_query_service.close()
    await app.state.macro_query_service.close()
    await app.state.dragon_tiger_query_service.close()
    await app.state.content_query_service.close()
    await app.state.fund_query_service.close()
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
    app.include_router(anomaly_router, prefix="/api/v1")
    app.include_router(market_router, prefix="/api/v1")
    app.include_router(gold_router, prefix="/api/v1")
    app.include_router(symbols_router, prefix="/api/v1")
    app.include_router(content_router, prefix="/api/v1")
    app.include_router(dragon_tiger_router, prefix="/api/v1")
    app.include_router(funds_router, prefix="/api/v1")
    app.include_router(macro_router, prefix="/api/v1")
    app.include_router(llm_audit_router, prefix="/api/v1")
    app.include_router(market_ws_router)
    return app


app = create_app()
