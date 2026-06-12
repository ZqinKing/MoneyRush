from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GlobalMarketRegionConfig:
    id: str
    display_name: str
    market_ids: tuple[str, ...]
    countries: tuple[str, ...]
    aggregation_label: str
    quote_currency: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "marketIds": list(self.market_ids),
            "countries": list(self.countries),
            "aggregationLabel": self.aggregation_label,
            "quoteCurrency": self.quote_currency,
        }


@dataclass(frozen=True, slots=True)
class GlobalMarketIndexMetadata:
    id: str
    display_name: str
    region: str
    country: str
    exchange: str
    longitude: float
    latitude: float
    display_order: int
    eastmoney_symbol: str | None
    fallback_symbol: str | None
    fallback_source_hint: str
    currency: str

    @property
    def coordinates(self) -> dict[str, float]:
        return {
            "longitude": self.longitude,
            "latitude": self.latitude,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "region": self.region,
            "country": self.country,
            "exchange": self.exchange,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "displayOrder": self.display_order,
            "eastmoneySymbol": self.eastmoney_symbol,
            "fallbackSymbol": self.fallback_symbol,
            "fallbackSourceHint": self.fallback_source_hint,
            "currency": self.currency,
        }


GLOBAL_MARKET_INDICES: tuple[GlobalMarketIndexMetadata, ...] = (
    GlobalMarketIndexMetadata(
        id="dow_jones",
        display_name="道琼斯工业平均指数",
        region="US",
        country="US",
        exchange="INDEXDJX",
        longitude=-74.0060,
        latitude=40.7128,
        display_order=1,
        eastmoney_symbol="100.DJIA",
        fallback_symbol="^DJI",
        fallback_source_hint="stooq-or-yahoo-finance",
        currency="USD",
    ),
    GlobalMarketIndexMetadata(
        id="sp500",
        display_name="标普500指数",
        region="US",
        country="US",
        exchange="SP",
        longitude=-74.0060,
        latitude=40.7128,
        display_order=2,
        eastmoney_symbol="100.SPX",
        fallback_symbol="^GSPC",
        fallback_source_hint="stooq-or-yahoo-finance",
        currency="USD",
    ),
    GlobalMarketIndexMetadata(
        id="nasdaq_composite",
        display_name="纳斯达克综合指数",
        region="US",
        country="US",
        exchange="NASDAQ",
        longitude=-74.0060,
        latitude=40.7128,
        display_order=3,
        eastmoney_symbol=None,
        fallback_symbol="^IXIC",
        fallback_source_hint="yahoo-finance-nasdaq-composite",
        currency="USD",
    ),
    GlobalMarketIndexMetadata(
        id="hang_seng",
        display_name="恒生指数",
        region="HK",
        country="HK",
        exchange="HKEX",
        longitude=114.1694,
        latitude=22.3193,
        display_order=4,
        eastmoney_symbol="100.HSI",
        fallback_symbol="^HSI",
        fallback_source_hint="stooq-or-yahoo-finance",
        currency="HKD",
    ),
    GlobalMarketIndexMetadata(
        id="shanghai_composite",
        display_name="上证指数",
        region="CN",
        country="CN",
        exchange="SSE",
        longitude=121.4737,
        latitude=31.2304,
        display_order=5,
        eastmoney_symbol="1.000001",
        fallback_symbol="000001.SH",
        fallback_source_hint="tencent-or-akshare",
        currency="CNY",
    ),
    GlobalMarketIndexMetadata(
        id="shenzhen_component",
        display_name="深证成指",
        region="CN",
        country="CN",
        exchange="SZSE",
        longitude=114.0579,
        latitude=22.5431,
        display_order=6,
        eastmoney_symbol="0.399001",
        fallback_symbol="399001.SZ",
        fallback_source_hint="tencent-or-akshare",
        currency="CNY",
    ),
    GlobalMarketIndexMetadata(
        id="csi300",
        display_name="沪深300指数",
        region="CN",
        country="CN",
        exchange="SSE/SZSE",
        longitude=121.4737,
        latitude=31.2304,
        display_order=7,
        eastmoney_symbol="1.000300",
        fallback_symbol="000300.SH",
        fallback_source_hint="tencent-or-akshare",
        currency="CNY",
    ),
    GlobalMarketIndexMetadata(
        id="nikkei_225",
        display_name="日经225指数",
        region="JP",
        country="JP",
        exchange="TSE",
        longitude=139.6917,
        latitude=35.6895,
        display_order=8,
        eastmoney_symbol=None,
        fallback_symbol="^N225",
        fallback_source_hint="moex-iss",
        currency="JPY",
    ),
    GlobalMarketIndexMetadata(
        id="kospi",
        display_name="韩国KOSPI指数",
        region="KR",
        country="KR",
        exchange="KRX",
        longitude=126.9780,
        latitude=37.5665,
        display_order=9,
        eastmoney_symbol=None,
        fallback_symbol="^KS11",
        fallback_source_hint="moex-iss",
        currency="KRW",
    ),
    GlobalMarketIndexMetadata(
        id="ftse_100",
        display_name="英国富时100指数",
        region="GB",
        country="GB",
        exchange="LSE",
        longitude=-0.1276,
        latitude=51.5072,
        display_order=10,
        eastmoney_symbol=None,
        fallback_symbol="^FTSE",
        fallback_source_hint="yahoo-finance",
        currency="GBP",
    ),
    GlobalMarketIndexMetadata(
        id="dax",
        display_name="德国DAX指数",
        region="DE",
        country="DE",
        exchange="XETRA",
        longitude=8.6821,
        latitude=50.1109,
        display_order=11,
        eastmoney_symbol=None,
        fallback_symbol="^GDAXI",
        fallback_source_hint="yahoo-finance",
        currency="EUR",
    ),
    GlobalMarketIndexMetadata(
        id="cac40",
        display_name="法国CAC40指数",
        region="FR",
        country="FR",
        exchange="EURONEXT",
        longitude=2.3522,
        latitude=48.8566,
        display_order=12,
        eastmoney_symbol=None,
        fallback_symbol="^FCHI",
        fallback_source_hint="yahoo-finance",
        currency="EUR",
    ),
    GlobalMarketIndexMetadata(
        id="moex_russia",
        display_name="俄罗斯MOEX指数",
        region="RU",
        country="RU",
        exchange="MOEX",
        longitude=37.6173,
        latitude=55.7558,
        display_order=13,
        eastmoney_symbol=None,
        fallback_symbol="IMOEX.ME",
        fallback_source_hint="yahoo-finance",
        currency="RUB",
    ),
    GlobalMarketIndexMetadata(
        id="sensex",
        display_name="印度SENSEX指数",
        region="IN",
        country="IN",
        exchange="BSE",
        longitude=72.8777,
        latitude=19.0760,
        display_order=14,
        eastmoney_symbol=None,
        fallback_symbol="^BSESN",
        fallback_source_hint="yahoo-finance",
        currency="INR",
    ),
    GlobalMarketIndexMetadata(
        id="ibovespa",
        display_name="巴西IBOVESPA指数",
        region="BR",
        country="BR",
        exchange="B3",
        longitude=-46.6333,
        latitude=-23.5505,
        display_order=15,
        eastmoney_symbol=None,
        fallback_symbol="^BVSP",
        fallback_source_hint="yahoo-finance",
        currency="BRL",
    ),
)

GLOBAL_MARKET_BY_ID: dict[str, GlobalMarketIndexMetadata] = {item.id: item for item in GLOBAL_MARKET_INDICES}

GLOBAL_MARKET_REGIONS: dict[str, GlobalMarketRegionConfig] = {
    "US": GlobalMarketRegionConfig(
        id="US",
        display_name="美股",
        market_ids=("dow_jones", "sp500", "nasdaq_composite"),
        countries=("US",),
        aggregation_label="美国市场",
        quote_currency="USD",
    ),
    "HK": GlobalMarketRegionConfig(
        id="HK",
        display_name="港股",
        market_ids=("hang_seng",),
        countries=("HK",),
        aggregation_label="香港市场",
        quote_currency="HKD",
    ),
    "CN": GlobalMarketRegionConfig(
        id="CN",
        display_name="A股",
        market_ids=("shanghai_composite", "shenzhen_component", "csi300"),
        countries=("CN",),
        aggregation_label="中国市场",
        quote_currency="CNY",
    ),
    "JP": GlobalMarketRegionConfig(
        id="JP",
        display_name="日股",
        market_ids=("nikkei_225",),
        countries=("JP",),
        aggregation_label="日本市场",
        quote_currency="JPY",
    ),
    "KR": GlobalMarketRegionConfig(
        id="KR",
        display_name="韩股",
        market_ids=("kospi",),
        countries=("KR",),
        aggregation_label="韩国市场",
        quote_currency="KRW",
    ),
    "GB": GlobalMarketRegionConfig(
        id="GB",
        display_name="英股",
        market_ids=("ftse_100",),
        countries=("GB",),
        aggregation_label="英国市场",
        quote_currency="GBP",
    ),
    "DE": GlobalMarketRegionConfig(
        id="DE",
        display_name="德股",
        market_ids=("dax",),
        countries=("DE",),
        aggregation_label="德国市场",
        quote_currency="EUR",
    ),
    "FR": GlobalMarketRegionConfig(
        id="FR",
        display_name="法股",
        market_ids=("cac40",),
        countries=("FR",),
        aggregation_label="法国市场",
        quote_currency="EUR",
    ),
    "RU": GlobalMarketRegionConfig(
        id="RU",
        display_name="俄股",
        market_ids=("moex_russia",),
        countries=("RU",),
        aggregation_label="俄罗斯市场",
        quote_currency="RUB",
    ),
    "IN": GlobalMarketRegionConfig(
        id="IN",
        display_name="印股",
        market_ids=("sensex",),
        countries=("IN",),
        aggregation_label="印度市场",
        quote_currency="INR",
    ),
    "BR": GlobalMarketRegionConfig(
        id="BR",
        display_name="巴股",
        market_ids=("ibovespa",),
        countries=("BR",),
        aggregation_label="巴西市场",
        quote_currency="BRL",
    ),
}

__all__ = [
    "GLOBAL_MARKET_BY_ID",
    "GLOBAL_MARKET_INDICES",
    "GLOBAL_MARKET_REGIONS",
    "GlobalMarketIndexMetadata",
    "GlobalMarketRegionConfig",
]
