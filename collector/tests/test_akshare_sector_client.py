from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from collector.services.akshare_sector_client import AkshareSectorClient
from collector.services.tencent_quote_client import MarketQuoteClient


class FakeFrame:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self._records: list[dict[str, object]] = records

    @property
    def empty(self) -> bool:
        return not self._records

    def copy(self) -> FakeFrame:
        return FakeFrame([record.copy() for record in self._records])

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return [record.copy() for record in self._records]


class FakeAkshare:
    def __init__(self, *, sz_records: list[dict[str, object]] | None = None) -> None:
        self._sz_records: list[dict[str, object]] = sz_records or [
            {"A股代码": "000001.SZ", "所属行业": "J 金融业"},
            {"A股代码": "000002.SZ", "所属行业": "K 房地产业"},
        ]

    def stock_info_sz_name_code(self, symbol: str) -> FakeFrame:
        assert symbol == "A股列表"
        return FakeFrame(self._sz_records)

    def stock_info_bj_name_code(self) -> FakeFrame:
        return FakeFrame([{"证券代码": "920000", "所属行业": "汽车制造业", "地区": "安徽省"}])

    def stock_profile_cninfo(self, symbol: str) -> FakeFrame:
        assert symbol == "600000"
        return FakeFrame([{"A股代码": "600000", "所属行业": "货币金融服务", "注册地址": "上海市中山东一路12号"}])


class ShenwanAkshare:
    def __init__(self, *, history_records: list[dict[str, object]] | None = None) -> None:
        self._official_stock_info: FakeAkshare = FakeAkshare()
        self._history_records: list[dict[str, object]] = history_records or [
            {
                "symbol": "000001",
                "industry_code": "480301",
                "start_date": date(2021, 1, 1),
                "update_time": date(2024, 1, 1),
            }
        ]

    def stock_info_sz_name_code(self, symbol: str) -> FakeFrame:
        return self._official_stock_info.stock_info_sz_name_code(symbol)

    def stock_info_bj_name_code(self) -> FakeFrame:
        return self._official_stock_info.stock_info_bj_name_code()

    def stock_profile_cninfo(self, symbol: str) -> FakeFrame:
        return self._official_stock_info.stock_profile_cninfo(symbol)

    def stock_industry_clf_hist_sw(self) -> FakeFrame:
        return FakeFrame(self._history_records)

    def sw_class_code_2021(self) -> FakeFrame:
        return FakeFrame([{"行业代码": "480301", "一级行业名称": "银行", "二级行业名称": "股份制银行Ⅱ", "三级行业名称": "股份制银行Ⅲ"}])


def test_fetches_shenzhen_industry_payload() -> None:
    sector_info = AkshareSectorClient(FakeAkshare()).fetch_sector_info("000001")

    assert sector_info is not None
    assert sector_info.to_payload()["source"] == "akshare-official-stock-info"
    assert sector_info.to_payload()["industry"] == "J 金融业"
    assert "sectorCode" not in sector_info.to_payload()


def test_prefers_shenwan_industry_payload() -> None:
    sector_info = AkshareSectorClient(ShenwanAkshare()).fetch_sector_info("000001")

    assert sector_info is not None
    payload = sector_info.to_payload()
    assert payload["source"] == "akshare-shenwan-industry"
    assert payload["industry"] == "股份制银行Ⅲ"
    assert payload["sectorCode"] == "480301"
    assert "region" not in payload
    shenwan = payload["shenwan"]
    assert isinstance(shenwan, dict)
    assert shenwan["version"] == "SW2021"
    assert shenwan["source"] == "swsresearch-public-file"
    assert shenwan["level1"] == {"code": "480000", "name": "银行"}
    assert shenwan["level2"] == {"code": "480300", "name": "股份制银行Ⅱ"}
    assert shenwan["level3"] == {"code": "480301", "name": "股份制银行Ⅲ"}


def test_uses_latest_shenwan_history_row() -> None:
    sector_info = AkshareSectorClient(
        ShenwanAkshare(
            history_records=[
                {
                    "symbol": "000001",
                    "industry_code": "850000.SI",
                    "start_date": date(2020, 1, 1),
                    "update_time": date(2021, 1, 1),
                },
                {
                    "symbol": "000001",
                    "industry_code": "480301",
                    "start_date": date(2021, 1, 1),
                    "update_time": date(2024, 1, 1),
                },
            ]
        )
    ).fetch_sector_info("000001")

    assert sector_info is not None
    assert sector_info.to_payload()["sectorCode"] == "480301"


def test_falls_back_when_shenwan_has_no_symbol_match() -> None:
    sector_info = AkshareSectorClient(ShenwanAkshare(history_records=[{"symbol": "999999", "industry_code": "480301"}])).fetch_sector_info("000001")

    assert sector_info is not None
    payload = sector_info.to_payload()
    assert payload["source"] == "akshare-official-stock-info"
    assert payload["industry"] == "J 金融业"
    assert "shenwan" not in payload


def test_fetches_beijing_industry_and_region_payload() -> None:
    sector_info = AkshareSectorClient(FakeAkshare()).fetch_sector_info("920000")

    assert sector_info is not None
    assert sector_info.to_payload()["industry"] == "汽车制造业"
    assert sector_info.to_payload()["region"] == "安徽省"


def test_fetches_shanghai_industry_without_address_region() -> None:
    sector_info = AkshareSectorClient(FakeAkshare()).fetch_sector_info("600000")

    assert sector_info is not None
    payload = sector_info.to_payload()
    assert payload["industry"] == "货币金融服务"
    assert "region" not in payload


def test_alias_selection_skips_empty_values() -> None:
    sector_info = AkshareSectorClient(FakeAkshare(sz_records=[{"A股代码": "000001", "所属行业": "--", "行业": "专用设备制造业", "地区": "-", "省份": "浙江省"}])).fetch_sector_info("000001")

    assert sector_info is not None
    assert sector_info.industry == "专用设备制造业"
    assert sector_info.region == "浙江省"


def test_market_quote_client_does_not_construct_sector_client_eagerly() -> None:
    with patch("collector.services.tencent_quote_client.AkshareSectorClient", side_effect=RuntimeError("missing akshare")):
        client = MarketQuoteClient(SimpleNamespace())
        try:
            assert _get_sector_info_or_none(client, "000001") is None
        finally:
            _shutdown_sector_executor(client)


def test_sector_timeout_degrades_to_missing_sector() -> None:
    class HangingClient:
        def fetch_sector_info(self, symbol: str) -> None:
            import time

            assert symbol == "000001"
            time.sleep(0.05)

    client = MarketQuoteClient(SimpleNamespace())
    try:
        with patch.object(client, "_get_sector_client", return_value=HangingClient()), patch("collector.services.tencent_quote_client.SECTOR_FETCH_TIMEOUT_SECONDS", 0.001):
            assert _get_sector_info_or_none(client, "000001") is None
    finally:
        _shutdown_sector_executor(client)


def _get_sector_info_or_none(client: MarketQuoteClient, symbol: str) -> dict[str, object] | None:
    fetch = cast(Callable[[str], dict[str, object] | None], getattr(client, "_get_sector_info_or_none"))
    return fetch(symbol)


def _shutdown_sector_executor(client: MarketQuoteClient) -> None:
    executor = cast(ThreadPoolExecutor, getattr(client, "_sector_executor"))
    executor.shutdown(cancel_futures=True)
