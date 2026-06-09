from __future__ import annotations

from types import SimpleNamespace
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
    def stock_info_sz_name_code(self, symbol: str) -> FakeFrame:
        assert symbol == "A股列表"
        return FakeFrame(
            [
                {"A股代码": "000001.SZ", "所属行业": "J 金融业"},
                {"A股代码": "000002.SZ", "所属行业": "K 房地产业"},
            ]
        )

    def stock_info_bj_name_code(self) -> FakeFrame:
        return FakeFrame([{"证券代码": "920000", "所属行业": "汽车制造业", "地区": "安徽省"}])

    def stock_profile_cninfo(self, symbol: str) -> FakeFrame:
        assert symbol == "600000"
        return FakeFrame([{"A股代码": "600000", "所属行业": "货币金融服务", "注册地址": "上海市中山东一路12号"}])


def test_fetches_shenzhen_industry_payload() -> None:
    sector_info = AkshareSectorClient(FakeAkshare()).fetch_sector_info("000001")

    assert sector_info is not None
    assert sector_info.to_payload()["source"] == "akshare-official-stock-info"
    assert sector_info.to_payload()["industry"] == "J 金融业"
    assert "sectorCode" not in sector_info.to_payload()


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
    class AliasAkshare(FakeAkshare):
        def stock_info_sz_name_code(self, symbol: str) -> FakeFrame:
            return FakeFrame([{"A股代码": "000001", "所属行业": "--", "行业": "专用设备制造业", "地区": "-", "省份": "浙江省"}])

    sector_info = AkshareSectorClient(AliasAkshare()).fetch_sector_info("000001")

    assert sector_info is not None
    assert sector_info.industry == "专用设备制造业"
    assert sector_info.region == "浙江省"


def test_market_quote_client_does_not_construct_sector_client_eagerly() -> None:
    with patch("collector.services.tencent_quote_client.AkshareSectorClient", side_effect=RuntimeError("missing akshare")):
        client = MarketQuoteClient(SimpleNamespace())
        try:
            assert client._get_sector_info_or_none("000001") is None
        finally:
            client._sector_executor.shutdown(cancel_futures=True)


def test_sector_timeout_degrades_to_missing_sector() -> None:
    class HangingClient:
        def fetch_sector_info(self, symbol: str) -> None:
            import time

            time.sleep(0.05)

    client = MarketQuoteClient(SimpleNamespace())
    try:
        with patch.object(client, "_get_sector_client", return_value=HangingClient()), patch("collector.services.tencent_quote_client.SECTOR_FETCH_TIMEOUT_SECONDS", 0.001):
            assert client._get_sector_info_or_none("000001") is None
    finally:
        client._sector_executor.shutdown(cancel_futures=True)
