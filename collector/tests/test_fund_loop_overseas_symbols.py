from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from collector.workers.fund_loop import FundCollectorWorker


def test_refresh_fund_registers_overseas_symbols_separately() -> None:
    redis = FakeRedis()
    postgres = FakePostgres()
    client = FakeFundClient()
    worker = _worker(redis=redis, postgres=postgres, client=client)

    asyncio.run(worker._refresh_fund("513500", auto_link_stocks=True))

    assert redis.sets["moneyrush:active_symbols"] == {"000001"}
    assert redis.sets["moneyrush:active_symbols:fund:513500"] == {"000001"}
    assert redis.sets["moneyrush:active_symbols:overseas"] == {"AAPL.US", "00700.HK"}
    assert redis.sets["moneyrush:active_symbols:overseas:fund:513500"] == {"AAPL.US", "00700.HK"}
    assert sorted(str(row["stock_symbol"]) for row in postgres.fund_stock_links) == ["000001", "00700.HK", "AAPL.US"]
    assert client.stock_fund_holder_symbols == ["000001"]


def test_refresh_fund_removes_unsupported_domestic_lane_symbol() -> None:
    redis = FakeRedis()
    redis.sets["moneyrush:active_symbols"] = {"000001", "005930"}
    redis.sets["moneyrush:active_symbols:fund:513500"] = {"000001", "005930"}
    postgres = FakePostgres()
    client = FakeFundClient(
        holdings=[
            {"fund_code": "513500", "stock_symbol": "000001", "stock_market": "SZ", "report_date": "2026-06-30"},
            {"fund_code": "513500", "stock_symbol": "005930", "stock_market": "KR", "report_date": "2026-06-30"},
        ]
    )
    worker = _worker(redis=redis, postgres=postgres, client=client)

    asyncio.run(worker._refresh_fund("513500", auto_link_stocks=True))

    assert redis.sets["moneyrush:active_symbols"] == {"000001"}
    assert redis.sets["moneyrush:active_symbols:fund:513500"] == {"000001"}
    assert sorted(str(row["stock_symbol"]) for row in postgres.fund_stock_links) == ["000001"]
    assert postgres.deleted_exclude_symbols_by_fund_code["513500"] == ["000001"]
    assert client.stock_fund_holder_symbols == ["000001"]


def test_deactivate_fund_removes_unshared_overseas_symbol() -> None:
    redis = FakeRedis()
    redis.values["moneyrush:fund:513500:holdings"] = json.dumps(["000001", "AAPL.US"])
    redis.sets["moneyrush:active_symbols"] = {"000001"}
    redis.sets["moneyrush:active_symbols:fund:513500"] = {"000001"}
    redis.sets["moneyrush:active_symbols:overseas"] = {"AAPL.US"}
    redis.sets["moneyrush:active_symbols:overseas:fund:513500"] = {"AAPL.US"}
    postgres = FakePostgres()
    worker = _worker(redis=redis, postgres=postgres, client=FakeFundClient())

    asyncio.run(worker._deactivate_fund_links("513500"))

    assert redis.sets["moneyrush:active_symbols"] == set()
    assert redis.sets["moneyrush:active_symbols:overseas"] == set()
    assert redis.sets["moneyrush:active_symbols:overseas:fund:513500"] == set()
    assert postgres.deleted_fund_codes == ["513500"]


def _worker(*, redis: "FakeRedis", postgres: "FakePostgres", client: "FakeFundClient") -> FundCollectorWorker:
    worker = FundCollectorWorker.__new__(FundCollectorWorker)
    setattr(worker, "_settings", _settings())
    setattr(worker, "_redis", redis)
    setattr(worker, "_postgres", postgres)
    setattr(worker, "_client", client)
    setattr(worker, "_last_refreshed_at", {})
    return worker


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        redis_url="redis://example/0",
        postgres_dsn="postgresql://example",
        collector_enable_runtime_data_repair=False,
        collector_poll_interval_seconds=5,
        fund_collector_fetch_timeout_seconds=1.0,
        active_symbols_key="moneyrush:active_symbols",
        active_overseas_symbols_key="moneyrush:active_symbols:overseas",
        active_funds_key="moneyrush:active_funds",
        fund_snapshot_key_prefix="moneyrush:fund:snapshot",
        fund_holdings_key_prefix="moneyrush:fund",
        fund_auto_link_stocks_key_prefix="moneyrush:fund:auto_link",
        stock_funds_key_prefix="moneyrush:stock",
    )


class FakeFundClient:
    def __init__(self, holdings: list[dict[str, object]] | None = None) -> None:
        self.stock_fund_holder_symbols: list[str] = []
        self._holdings = holdings

    def fetch_fund_state(self, fund_code: str) -> dict[str, object]:
        holdings = self._holdings or [
            {"fund_code": fund_code, "stock_symbol": "000001", "stock_market": "SZ", "report_date": "2026-06-30"},
            {"fund_code": fund_code, "stock_symbol": "AAPL.US", "stock_market": "US", "report_date": "2026-06-30"},
            {"fund_code": fund_code, "stock_symbol": "00700.HK", "stock_market": "HK", "report_date": "2026-06-30"},
        ]
        return {
            "profile": {"fundCode": fund_code, "fundName": "QDII Fund"},
            "snapshot": {"fundCode": fund_code, "fundName": "QDII Fund"},
            "nav_history": [],
            "holdings": holdings,
        }

    def fetch_stock_fund_holders(self, symbol: str) -> list[dict[str, object]]:
        self.stock_fund_holder_symbols.append(symbol)
        return []


class FakePostgres:
    def __init__(self) -> None:
        self.fund_stock_links: list[dict[str, object]] = []
        self.deleted_fund_codes: list[str] = []
        self.deleted_exclude_symbols_by_fund_code: dict[str, list[str] | None] = {}

    async def upsert_fund_profile(self, _profile: dict[str, object]) -> None:
        pass

    async def upsert_fund_snapshot(self, _snapshot: dict[str, object]) -> None:
        pass

    async def upsert_fund_nav_rows(self, _rows: list[dict[str, object]]) -> None:
        pass

    async def upsert_fund_holding_rows(self, _rows: list[dict[str, object]]) -> None:
        pass

    async def upsert_fund_stock_links(self, rows: list[dict[str, object]]) -> None:
        self.fund_stock_links.extend(rows)

    async def upsert_stock_fund_holding_rows(self, _rows: list[dict[str, object]]) -> None:
        pass

    async def delete_fund_stock_links(self, fund_code: str, exclude_stock_symbols: list[str] | None = None) -> None:
        self.deleted_fund_codes.append(fund_code)
        self.deleted_exclude_symbols_by_fund_code[fund_code] = exclude_stock_symbols

    async def has_other_fund_stock_links(self, *, stock_symbol: str, excluding_fund_code: str) -> bool:
        return False


class FakeRedis:
    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.values: dict[str, str] = {}

    async def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def srem(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).difference_update(values)

    async def sismember(self, key: str, value: str) -> bool:
        return value in self.sets.get(key, set())

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.values.pop(key, None)
