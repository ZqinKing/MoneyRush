from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.global_markets import router
from app.main import create_app


class FakeRedisStore:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self.payload = payload

    async def get_global_markets_latest(self) -> dict[str, object] | None:
        return self.payload


def create_test_app(payload: dict[str, object] | None) -> TestClient:
    app = FastAPI()
    app.state.redis_store = FakeRedisStore(payload)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_global_markets_latest_returns_cached_payload() -> None:
    client = create_test_app(
        {
            "items": [{"symbol": "SPX", "value": 5300.0}],
            "regions": ["US", "EU"],
            "source": "collector",
            "updatedAt": "2026-06-08T12:00:00Z",
            "delayLabel": "15分钟延迟",
            "stale": False,
            "errors": [],
            "extra": "ignored",
        }
    )

    response = client.get("/api/v1/global-markets/latest")

    assert response.status_code == 200
    assert response.json() == {
        "items": [{"symbol": "SPX", "value": 5300.0}],
        "regions": ["US", "EU"],
        "source": "collector",
        "updatedAt": "2026-06-08T12:00:00Z",
        "delayLabel": "15分钟延迟",
        "stale": False,
        "errors": [],
    }


def test_global_markets_latest_returns_503_when_cache_missing() -> None:
    client = create_test_app(None)

    response = client.get("/api/v1/global-markets/latest")

    assert response.status_code == 503
    assert response.json() == {"detail": "global markets cache unavailable"}


def test_global_markets_latest_preserves_stale_metadata() -> None:
    client = create_test_app(
        {
            "items": [{"symbol": "HSI", "value": 18200.5}],
            "regions": ["APAC"],
            "source": "collector",
            "updatedAt": "2026-06-08T08:00:00Z",
            "delayLabel": "已延迟",
            "stale": True,
            "errors": [{"region": "APAC", "message": "partial upstream timeout"}],
        }
    )

    response = client.get("/api/v1/global-markets/latest")

    assert response.status_code == 200
    assert response.json()["stale"] is True
    assert response.json()["delayLabel"] == "已延迟"
    assert response.json()["errors"] == [{"region": "APAC", "message": "partial upstream timeout"}]


def test_create_app_registers_global_markets_route() -> None:
    app = create_app()

    assert any(route.path == "/api/v1/global-markets/latest" for route in app.routes)


def test_lifespan_passes_global_markets_cache_key(monkeypatch) -> None:
    from app import main as main_module

    captured: dict[str, object] = {}

    class FakeRedisStoreInit:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def close(self) -> None:
            return None

    class FakeService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def connect(self) -> None:
            return None

        async def close(self) -> None:
            return None

    class FakeDragonTigerClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(main_module, "RedisStore", FakeRedisStoreInit)
    monkeypatch.setattr(main_module, "MarketDetailQueryService", FakeService)
    monkeypatch.setattr(main_module, "FundQueryService", FakeService)
    monkeypatch.setattr(main_module, "ContentQueryService", FakeService)
    monkeypatch.setattr(main_module, "DragonTigerQueryService", FakeService)
    monkeypatch.setattr(main_module, "MacroQueryService", FakeService)
    monkeypatch.setattr(main_module, "LlmAuditQueryService", FakeService)
    monkeypatch.setattr(main_module, "TimelineQueryService", FakeService)
    monkeypatch.setattr(main_module, "MacroAnalysisService", FakeService)
    monkeypatch.setattr(main_module, "FundPortfolioRiskAnalysisService", FakeService)
    monkeypatch.setattr(main_module, "SymbolLookupService", lambda: SimpleNamespace())
    monkeypatch.setattr(main_module, "FundLookupService", lambda: SimpleNamespace())
    monkeypatch.setattr(main_module, "DragonTigerClient", FakeDragonTigerClient)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: SimpleNamespace(
            app_name="MoneyRush API",
            app_env="test",
            frontend_origin="http://localhost:5173",
            frontend_origin_regex=None,
            redis_url="redis://localhost:6379/0",
            stream_key="unused",
            redis_stream_key="moneyrush:symbol:commands",
            active_symbols_key="moneyrush:active_symbols",
            market_snapshot_key_prefix="moneyrush:snapshot",
            market_event_key_prefix="moneyrush:event",
            market_events_stream_key="moneyrush:market:events",
            market_overview_cache_key="moneyrush:market:overview",
            global_markets_cache_key="moneyrush:global_markets:latest",
            gold_dashboard_cache_key="moneyrush:gold:dashboard",
            active_funds_key="moneyrush:active_funds",
            fund_snapshot_key_prefix="moneyrush:fund:snapshot",
            fund_holdings_key_prefix="moneyrush:fund",
            fund_auto_link_stocks_key_prefix="moneyrush:fund:auto_link",
            stock_funds_key_prefix="moneyrush:stock",
            content_feed_cache_key_prefix="moneyrush:content:feed",
            content_status_cache_key_prefix="moneyrush:content:status",
            dragon_tiger_cache_key_prefix="moneyrush:dragon_tiger",
            macro_snapshot_cache_key="moneyrush:macro:snapshot",
            macro_analysis_latest_cache_key="moneyrush:macro:analysis:latest",
            macro_collector_status_cache_key="moneyrush:macro:collector_status",
            content_report_refresh_seconds=43200,
            content_news_refresh_seconds=1800,
            content_announcement_refresh_seconds=7200,
            content_market_news_refresh_seconds=900,
            postgres_dsn="postgresql://moneyrush:moneyrush@db:5432/moneyrush",
            dragon_tiger_request_timeout_seconds=15.0,
            dragon_tiger_request_retry_attempts=3,
            dragon_tiger_request_retry_backoff_seconds=0.6,
        ),
    )

    app = FastAPI()

    async def run_lifespan() -> None:
        async with main_module.lifespan(app):
            assert captured["global_markets_cache_key"] == "moneyrush:global_markets:latest"

    import asyncio

    asyncio.run(run_lifespan())
