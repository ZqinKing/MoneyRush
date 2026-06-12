from collector.services.global_markets_contract import (
    GLOBAL_MARKET_BY_ID,
    GLOBAL_MARKET_INDICES,
    GLOBAL_MARKET_REGIONS,
)


EXPECTED_MARKET_IDS = (
    "dow_jones",
    "sp500",
    "nasdaq_composite",
    "hang_seng",
    "shanghai_composite",
    "shenzhen_component",
    "csi300",
    "nikkei_225",
    "kospi",
    "ftse_100",
    "dax",
    "cac40",
    "moex_russia",
    "sensex",
    "ibovespa",
)


def test_global_market_indices_have_exact_canonical_ids() -> None:
    assert tuple(index.id for index in GLOBAL_MARKET_INDICES) == EXPECTED_MARKET_IDS
    assert tuple(GLOBAL_MARKET_BY_ID) == EXPECTED_MARKET_IDS


def test_global_market_indices_keep_canonical_coordinates() -> None:
    assert GLOBAL_MARKET_BY_ID["dow_jones"].coordinates == {
        "longitude": -74.0060,
        "latitude": 40.7128,
    }
    assert GLOBAL_MARKET_BY_ID["sp500"].coordinates == {
        "longitude": -74.0060,
        "latitude": 40.7128,
    }
    assert GLOBAL_MARKET_BY_ID["nasdaq_composite"].coordinates == {
        "longitude": -74.0060,
        "latitude": 40.7128,
    }
    assert GLOBAL_MARKET_BY_ID["hang_seng"].coordinates == {
        "longitude": 114.1694,
        "latitude": 22.3193,
    }
    assert GLOBAL_MARKET_BY_ID["shanghai_composite"].coordinates == {
        "longitude": 121.4737,
        "latitude": 31.2304,
    }
    assert GLOBAL_MARKET_BY_ID["shenzhen_component"].coordinates == {
        "longitude": 114.0579,
        "latitude": 22.5431,
    }
    assert GLOBAL_MARKET_BY_ID["csi300"].coordinates == {
        "longitude": 121.4737,
        "latitude": 31.2304,
    }
    assert GLOBAL_MARKET_BY_ID["nikkei_225"].coordinates == {
        "longitude": 139.6917,
        "latitude": 35.6895,
    }
    assert GLOBAL_MARKET_BY_ID["kospi"].coordinates == {
        "longitude": 126.978,
        "latitude": 37.5665,
    }
    assert GLOBAL_MARKET_BY_ID["ftse_100"].coordinates == {
        "longitude": -0.1276,
        "latitude": 51.5072,
    }
    assert GLOBAL_MARKET_BY_ID["dax"].coordinates == {
        "longitude": 8.6821,
        "latitude": 50.1109,
    }
    assert GLOBAL_MARKET_BY_ID["cac40"].coordinates == {
        "longitude": 2.3522,
        "latitude": 48.8566,
    }
    assert GLOBAL_MARKET_BY_ID["moex_russia"].coordinates == {
        "longitude": 37.6173,
        "latitude": 55.7558,
    }
    assert GLOBAL_MARKET_BY_ID["sensex"].coordinates == {
        "longitude": 72.8777,
        "latitude": 19.076,
    }
    assert GLOBAL_MARKET_BY_ID["ibovespa"].coordinates == {
        "longitude": -46.6333,
        "latitude": -23.5505,
    }


def test_nasdaq_composite_uses_composite_fallback_not_ndx() -> None:
    nasdaq_composite = GLOBAL_MARKET_BY_ID["nasdaq_composite"]

    assert nasdaq_composite.eastmoney_symbol is None
    assert nasdaq_composite.fallback_symbol is not None
    assert "NDX" not in nasdaq_composite.fallback_symbol.upper()
    assert "IXIC" in nasdaq_composite.fallback_symbol.upper()


def test_moex_russia_uses_verified_yahoo_fallback_provider() -> None:
    moex_russia = GLOBAL_MARKET_BY_ID["moex_russia"]

    assert moex_russia.eastmoney_symbol is None
    assert moex_russia.fallback_symbol == "IMOEX.ME"
    assert moex_russia.fallback_source_hint == "yahoo-finance"


def test_region_membership_matches_canonical_markets() -> None:
    assert tuple(GLOBAL_MARKET_REGIONS) == ("US", "HK", "CN", "JP", "KR", "GB", "DE", "FR", "RU", "IN", "BR")
    assert GLOBAL_MARKET_REGIONS["US"].market_ids == (
        "dow_jones",
        "sp500",
        "nasdaq_composite",
    )
    assert GLOBAL_MARKET_REGIONS["HK"].market_ids == ("hang_seng",)
    assert GLOBAL_MARKET_REGIONS["CN"].market_ids == (
        "shanghai_composite",
        "shenzhen_component",
        "csi300",
    )
    assert GLOBAL_MARKET_REGIONS["JP"].market_ids == ("nikkei_225",)
    assert GLOBAL_MARKET_REGIONS["KR"].market_ids == ("kospi",)
    assert GLOBAL_MARKET_REGIONS["GB"].market_ids == ("ftse_100",)
    assert GLOBAL_MARKET_REGIONS["DE"].market_ids == ("dax",)
    assert GLOBAL_MARKET_REGIONS["FR"].market_ids == ("cac40",)
    assert GLOBAL_MARKET_REGIONS["RU"].market_ids == ("moex_russia",)
    assert GLOBAL_MARKET_REGIONS["IN"].market_ids == ("sensex",)
    assert GLOBAL_MARKET_REGIONS["BR"].market_ids == ("ibovespa",)
    assert set().union(*(region.market_ids for region in GLOBAL_MARKET_REGIONS.values())) == set(
        EXPECTED_MARKET_IDS
    )


def test_market_to_dict_uses_normalized_camel_case_shape() -> None:
    market_payload = GLOBAL_MARKET_BY_ID["nasdaq_composite"].to_dict()

    assert market_payload == {
        "id": "nasdaq_composite",
        "displayName": "纳斯达克综合指数",
        "region": "US",
        "country": "US",
        "exchange": "NASDAQ",
        "longitude": -74.0060,
        "latitude": 40.7128,
        "displayOrder": 3,
        "eastmoneySymbol": None,
        "fallbackSymbol": "^IXIC",
        "fallbackSourceHint": "yahoo-finance-nasdaq-composite",
        "currency": "USD",
    }


def test_region_to_dict_uses_normalized_camel_case_shape() -> None:
    region_payload = GLOBAL_MARKET_REGIONS["US"].to_dict()

    assert region_payload == {
        "id": "US",
        "displayName": "美股",
        "marketIds": ["dow_jones", "sp500", "nasdaq_composite"],
        "countries": ["US"],
        "aggregationLabel": "美国市场",
        "quoteCurrency": "USD",
    }
