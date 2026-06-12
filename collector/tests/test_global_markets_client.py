from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from math import isclose
from types import SimpleNamespace
from typing import cast

from collector.services.global_markets_client import (
    GlobalMarketsClient,
    is_snapshot_stale,
    merge_provider_snapshots,
    parse_eastmoney_quote,
    parse_fallback_quote,
    parse_moex_iss_quote,
    parse_yahoo_quote_page,
)
from collector.workers.global_markets_loop import GlobalMarketsCollectorWorker


NOW = datetime(2026, 6, 8, 9, 30, tzinfo=UTC)

EASTMONEY_FIXTURE: dict[str, object] = {
    "data": {
        "diff": [
            {
                "f12": "DJIA",
                "f13": "100",
                "f2": "38662.25",
                "f3": "0.18",
                "f4": "69.05",
                "f124": "2026-06-08T09:20:00+00:00",
                "marketStatus": "trading",
                "delayLabel": "15分钟延迟",
            },
            {
                "f12": "SPX",
                "f13": "100",
                "f2": "--",
                "f3": "bad",
                "f4": None,
            },
        ]
    }
}

YAHOO_QUOTE_FIXTURE: dict[str, object] = {
    "quoteResponse": {
        "result": [
            {
                "symbol": "^IXIC",
                "regularMarketPrice": 17133.13,
                "regularMarketChange": -14.81,
                "regularMarketChangePercent": -0.0864,
                "currency": "USD",
                "marketState": "REGULAR",
                "exchangeTimezoneShortName": "EDT",
                "regularMarketTime": 1780910700,
                "source": "yahoo-finance-nasdaq-composite",
            }
        ]
    }
}

YAHOO_CHART_FIXTURE: dict[str, object] = {
    "chart": {
        "result": [
            {
                "meta": {
                    "symbol": "^GSPC",
                    "regularMarketPrice": 5362.12,
                    "previousClose": 5350.0,
                    "currency": "USD",
                    "marketState": "POST",
                    "exchangeTimezoneShortName": "EDT",
                    "regularMarketTime": 1780910700,
                    "source": "yahoo-finance",
                },
                "timestamp": [1780910400, 1780910700],
                "indicators": {"quote": [{"close": [5358.0, 5362.12]}]},
            }
        ]
    }
}

YAHOO_NASDAQ_PAGE_HTML = """
<html>
  <head><title>NASDAQ Composite (^IXIC)</title></head>
  <body>
    <section data-symbol="^IXIC">
      <h1 title="NASDAQ Composite">NASDAQ Composite</h1>
      <fin-streamer data-field="regularMarketPrice">25,929.66</fin-streamer>
      <fin-streamer data-field="regularMarketChange">221.11</fin-streamer>
      <fin-streamer data-field="regularMarketChangePercent">(+0.86%)</fin-streamer>
    </section>
  </body>
</html>
"""

YAHOO_NASDAQ_PAGE_WITH_STRAY_QUOTE_HTML = """
<html>
  <head><title>NASDAQ Composite (^IXIC)</title></head>
  <body>
    <script>
      window.relatedQuotes = {
        "trackingSymbol": "^IXIC",
        "items": [{
          "symbol": "APP",
          "shortName": "AppLovin Corporation",
          "regularMarketPrice": {"raw": 39.49, "fmt": "39.49"},
          "regularMarketChangePercent": {"raw": 3660.94, "fmt": "+3,660.94%"}
        }]
      };
      window.quoteStore = {
        "price": {
          "symbol": "^IXIC",
          "shortName": "NASDAQ Composite",
          "regularMarketPrice": {"raw": 25929.662, "fmt": "25,929.66"},
          "regularMarketChange": {"raw": 221.11, "fmt": "+221.11"},
          "regularMarketChangePercent": {"raw": 0.86, "fmt": "+0.86%"},
          "currency": "USD",
          "marketState": "REGULAR",
          "exchangeTimezoneShortName": "EDT"
        }
      };
    </script>
  </body>
</html>
"""

YAHOO_NIKKEI_PAGE_HTML = """
<html>
  <head><title>Nikkei 225 (^N225)</title></head>
  <body>
    <section data-symbol="^N225">
      <h1 title="Nikkei 225">Nikkei 225</h1>
      <fin-streamer data-field="regularMarketPrice">38,088.57</fin-streamer>
      <fin-streamer data-field="regularMarketChange">112.25</fin-streamer>
      <fin-streamer data-field="regularMarketChangePercent">(+0.30%)</fin-streamer>
    </section>
  </body>
</html>
"""

YAHOO_KOSPI_PAGE_HTML = """
<html>
  <head><title>KOSPI Composite Index (^KS11)</title></head>
  <body>
    <script>
      window.relatedQuotes = {
        "trackingSymbol": "^KS11",
        "items": [{
          "symbol": "005930.KS",
          "shortName": "Samsung Electronics Co., Ltd.",
          "regularMarketPrice": {"raw": 72800, "fmt": "72,800.00"}
        }]
      };
      window.quoteStore = {
        "price": {
          "symbol": "^KS11",
          "shortName": "KOSPI Composite Index",
          "regularMarketPrice": {"raw": 2920.03, "fmt": "2,920.03"},
          "regularMarketChange": {"raw": -8.54, "fmt": "-8.54"},
          "regularMarketChangePercent": {"raw": -0.29, "fmt": "-0.29%"},
          "currency": "KRW",
          "marketState": "REGULAR",
          "exchangeTimezoneShortName": "KST"
        }
      };
    </script>
  </body>
</html>
"""

YAHOO_NIKKEI_PAGE_WITH_RELATED_KOSPI_HTML = """
<html>
  <head><title>Nikkei 225 (^N225) Stock Price, News, Quote & History - Yahoo Finance</title></head>
  <body>
    <script>
      window.quoteStore = {
        "price": {
          "symbol": "^N225",
          "shortName": "Nikkei 225",
          "regularMarketPrice": {"raw": 38088.57, "fmt": "38,088.57"},
          "regularMarketChange": {"raw": 112.25, "fmt": "+112.25"},
          "regularMarketChangePercent": {"raw": 0.30, "fmt": "+0.30%"},
          "currency": "JPY",
          "marketState": "REGULAR",
          "exchangeTimezoneShortName": "JST"
        },
        "relatedQuotes": [{
          "symbol": "^KS11",
          "shortName": "KOSPI Composite Index",
          "regularMarketPrice": {"raw": 8045.69, "fmt": "8,045.69"},
          "regularMarketChangePercent": {"raw": 2.11, "fmt": "+2.11%"},
          "marketState": "REGULAR",
          "exchangeTimezoneShortName": "KST"
        }]
      };
    </script>
  </body>
</html>
"""

YAHOO_KOSPI_PAGE_WITH_RELATED_NIKKEI_HTML = """
<html>
  <head><title>KOSPI Composite Index (^KS11) Stock Price, News, Quote & History - Yahoo Finance</title></head>
  <body>
    <script>
      window.quoteStore = {
        "relatedQuotes": [{
          "symbol": "^N225",
          "shortName": "Nikkei 225",
          "regularMarketPrice": {"raw": 65472.67, "fmt": "65,472.67"},
          "regularMarketChangePercent": {"raw": 1.44, "fmt": "+1.44%"},
          "marketState": "REGULAR",
          "exchangeTimezoneShortName": "JST"
        }],
        "price": {
          "symbol": "^KS11",
          "shortName": "KOSPI Composite Index",
          "regularMarketPrice": {"raw": 2920.03, "fmt": "2,920.03"},
          "regularMarketChange": {"raw": -8.54, "fmt": "-8.54"},
          "regularMarketChangePercent": {"raw": -0.29, "fmt": "-0.29%"},
          "currency": "KRW",
          "marketState": "REGULAR",
          "exchangeTimezoneShortName": "KST"
        }
      };
    </script>
  </body>
</html>
"""

YAHOO_KOSPI_PAGE_WITH_QUOTE_PRICE_SECTION_HTML = """
<html>
  <head><title>KOSPI Composite Index (^KS11) Stock Price, News, Quote & History - Yahoo Finance</title></head>
  <body>
    <script>
      window.trending = {"symbol":"APP","shortName":"AppLovin Corporation","regularMarketPrice":{"raw":39.49}};
      window.relatedQuotes = [{
        "symbol": "^N225",
        "shortName": "Nikkei 225",
        "regularMarketPrice": {"raw": 65472.67, "fmt": "65,472.67"}
      }];
    </script>
    <section data-testid="quote-price">
      <span class="price yf-ipw1h0" data-testid="qsp-price">8,041.35 </span>
      <span data-testid="qsp-price-change">+556.94 </span>
      <span data-testid="qsp-price-change-percent">(+7.44%) </span>
    </section>
  </body>
</html>
"""

YAHOO_FTSE_PAGE_HTML = """
<html>
  <head><title>FTSE 100 (^FTSE) Charts, Data &amp; News - Yahoo Finance</title></head>
  <body>
    <section data-testid="quote-price">
      <span data-testid="qsp-price">8,842.45 </span>
      <span data-testid="qsp-price-change">+52.33 </span>
      <span data-testid="qsp-price-change-percent">(+0.60%) </span>
    </section>
  </body>
</html>
"""

YAHOO_DAX_PAGE_HTML = """
<html>
  <head><title>DAX P (^GDAXI) Charts, Data &amp; News - Yahoo Finance</title></head>
  <body>
    <section data-testid="quote-price">
      <span data-testid="qsp-price">24,155.50 </span>
      <span data-testid="qsp-price-change">-48.11 </span>
      <span data-testid="qsp-price-change-percent">(-0.20%) </span>
    </section>
  </body>
</html>
"""

YAHOO_CAC40_PAGE_HTML = """
<html>
  <head><title>CAC 40 (^FCHI) Charts, Data &amp; News - Yahoo Finance</title></head>
  <body>
    <section data-testid="quote-price">
      <span data-testid="qsp-price">7,729.32 </span>
      <span data-testid="qsp-price-change">+12.20 </span>
      <span data-testid="qsp-price-change-percent">(+0.16%) </span>
    </section>
  </body>
</html>
"""

YAHOO_SENSEX_PAGE_HTML = """
<html>
  <head><title>S&amp;P BSE SENSEX (^BSESN) Charts, Data &amp; News - Yahoo Finance</title></head>
  <body>
    <section data-testid="quote-price">
      <span data-testid="qsp-price">82,445.21 </span>
      <span data-testid="qsp-price-change">+190.12 </span>
      <span data-testid="qsp-price-change-percent">(+0.23%) </span>
    </section>
  </body>
</html>
"""

YAHOO_IBOVESPA_PAGE_HTML = """
<html>
  <head><title>IBOVESPA (^BVSP) Charts, Data &amp; News - Yahoo Finance</title></head>
  <body>
    <section data-testid="quote-price">
      <span data-testid="qsp-price">136,102.80 </span>
      <span data-testid="qsp-price-change">-420.15 </span>
      <span data-testid="qsp-price-change-percent">(-0.31%) </span>
    </section>
  </body>
</html>
"""

MOEX_ISS_FIXTURE: dict[str, object] = {
    "marketdata": {
        "columns": ["SECID", "CURRENTVALUE", "LASTCHANGE", "LASTCHANGEPRC", "TIME", "UPDATETIME"],
        "data": [["IMOEX", 2842.11, 18.45, 0.65, "19:00:00", "19:00:11"]],
    }
}


def test_global_markets_parser_normalizes_eastmoney_quote() -> None:
    snapshot = parse_eastmoney_quote("dow_jones", EASTMONEY_FIXTURE, now=NOW)

    assert snapshot is not None
    assert snapshot.to_dict() == {
        "id": "dow_jones",
        "symbol": "^DJI",
        "providerSymbol": "100.DJIA",
        "name": "道琼斯工业平均指数",
        "region": "US",
        "country": "US",
        "exchange": "INDEXDJX",
        "longitude": -74.006,
        "latitude": 40.7128,
        "price": 38662.25,
        "change": 69.05,
        "changePercent": 0.18,
        "currency": "USD",
        "marketStatus": "trading",
        "source": "eastmoney",
        "delayLabel": "15分钟延迟",
        "updatedAt": "2026-06-08T09:20:00+00:00",
        "stale": False,
    }


def test_global_markets_parser_handles_missing_or_malformed_vendor_fields() -> None:
    assert parse_eastmoney_quote("sp500", EASTMONEY_FIXTURE, now=NOW) is None
    assert parse_eastmoney_quote("unknown", EASTMONEY_FIXTURE, now=NOW) is None
    assert parse_fallback_quote("sp500", {"symbol": "^GSPC", "regularMarketPrice": "--"}, now=NOW) is None


def test_global_markets_parser_skips_nasdaq_composite_eastmoney_without_symbol() -> None:
    assert parse_eastmoney_quote("nasdaq_composite", EASTMONEY_FIXTURE, now=NOW) is None


def test_global_markets_fallback_supplies_nasdaq_from_ixic() -> None:
    snapshot = parse_fallback_quote("nasdaq_composite", YAHOO_QUOTE_FIXTURE, now=NOW)

    assert snapshot is not None
    payload = snapshot.to_dict()
    assert payload["id"] == "nasdaq_composite"
    assert payload["symbol"] == "^IXIC"
    assert payload["providerSymbol"] == "^IXIC"
    assert payload["source"] == "yahoo-finance-nasdaq-composite"
    assert payload["price"] == 17133.13
    assert payload["change"] == -14.81
    assert payload["changePercent"] == -0.0864
    assert payload["currency"] == "USD"
    assert payload["marketStatus"] == "REGULAR"
    assert payload["delayLabel"] == "EDT"


def test_global_markets_fallback_supplies_moex_from_imoex_me() -> None:
    snapshot = parse_fallback_quote(
        "moex_russia",
        {
            "quoteResponse": {
                "result": [
                    _yahoo_quote_row("IMOEX.ME", 2842.11, 18.45, 0.65, "RUB", NOW),
                ]
            }
        },
        now=NOW,
    )

    assert snapshot is not None
    payload = snapshot.to_dict()
    assert payload["id"] == "moex_russia"
    assert payload["symbol"] == "IMOEX.ME"
    assert payload["providerSymbol"] == "IMOEX.ME"
    assert payload["source"] == "yahoo-finance"
    assert payload["price"] == 2842.11
    assert payload["change"] == 18.45
    assert payload["changePercent"] == 0.65
    assert payload["currency"] == "RUB"


def test_global_markets_fallback_supplies_moex_from_moex_iss() -> None:
    snapshot = parse_moex_iss_quote(MOEX_ISS_FIXTURE, now=NOW)

    assert snapshot is not None
    payload = snapshot.to_dict()
    assert payload["id"] == "moex_russia"
    assert payload["symbol"] == "IMOEX.ME"
    assert payload["providerSymbol"] == "IMOEX"
    assert payload["source"] == "moex-iss"
    assert payload["delayLabel"] == "MOEX ISS"
    assert payload["price"] == 2842.11
    assert payload["change"] == 18.45
    assert payload["changePercent"] == 0.65
    assert payload["currency"] == "RUB"


def test_global_markets_fallback_normalizes_yahoo_chart_payload() -> None:
    snapshot = parse_fallback_quote("sp500", YAHOO_CHART_FIXTURE, now=NOW)

    assert snapshot is not None
    payload = snapshot.to_dict()
    assert payload["id"] == "sp500"
    assert payload["providerSymbol"] == "^GSPC"
    assert payload["source"] == "yahoo-finance"
    assert payload["price"] == 5362.12
    assert payload["change"] == 12.11999999999989
    assert isinstance(payload["changePercent"], float)
    assert isclose(payload["changePercent"], 0.2265420560747643)


def test_global_markets_fallback_parses_nasdaq_yahoo_quote_page() -> None:
    snapshot = parse_yahoo_quote_page("nasdaq_composite", YAHOO_NASDAQ_PAGE_HTML, now=NOW)

    assert snapshot is not None
    payload = snapshot.to_dict()
    assert payload["id"] == "nasdaq_composite"
    assert payload["symbol"] == "^IXIC"
    assert payload["providerSymbol"] == "^IXIC"
    assert payload["source"] == "yahoo-finance-quote-page-nasdaq-composite"
    assert payload["price"] == 25929.66
    assert payload["change"] == 221.11
    assert payload["changePercent"] == 0.86


def test_global_markets_fallback_ignores_stray_quote_near_ixic_marker() -> None:
    snapshot = parse_yahoo_quote_page("nasdaq_composite", YAHOO_NASDAQ_PAGE_WITH_STRAY_QUOTE_HTML, now=NOW)

    assert snapshot is not None
    payload = snapshot.to_dict()
    assert payload["providerSymbol"] == "^IXIC"
    assert payload["source"] == "yahoo-finance-quote-page-nasdaq-composite"
    assert payload["price"] == 25929.662
    assert payload["changePercent"] == 0.86


def test_global_markets_fallback_parses_japan_korea_yahoo_quote_pages() -> None:
    nikkei = parse_yahoo_quote_page("nikkei_225", YAHOO_NIKKEI_PAGE_HTML, now=NOW)
    kospi = parse_yahoo_quote_page("kospi", YAHOO_KOSPI_PAGE_HTML, now=NOW)

    assert nikkei is not None
    assert kospi is not None
    nikkei_payload = nikkei.to_dict()
    kospi_payload = kospi.to_dict()
    assert nikkei_payload["id"] == "nikkei_225"
    assert nikkei_payload["name"] == "日经225指数"
    assert nikkei_payload["region"] == "JP"
    assert nikkei_payload["providerSymbol"] == "^N225"
    assert nikkei_payload["source"] == "yahoo-finance-quote-page-nikkei-225"
    assert nikkei_payload["price"] == 38088.57
    assert nikkei_payload["changePercent"] == 0.30
    assert kospi_payload["id"] == "kospi"
    assert kospi_payload["name"] == "韩国KOSPI指数"
    assert kospi_payload["region"] == "KR"
    assert kospi_payload["providerSymbol"] == "^KS11"
    assert kospi_payload["source"] == "yahoo-finance-quote-page-kospi"
    assert kospi_payload["price"] == 2920.03
    assert kospi_payload["changePercent"] == -0.29


def test_global_markets_fallback_rejects_unrelated_japan_korea_page_quotes() -> None:
    unrelated = """
    <html><body><script>{"symbol":"^N225","shortName":"Random Watchlist","regularMarketPrice":{"raw":1}}</script></body></html>
    """

    assert parse_yahoo_quote_page("nikkei_225", unrelated, now=NOW) is None
    assert parse_yahoo_quote_page("kospi", YAHOO_NIKKEI_PAGE_HTML, now=NOW) is None


def test_global_markets_fallback_prefers_current_japan_korea_page_quote_over_related_cards() -> None:
    nikkei = parse_yahoo_quote_page("nikkei_225", YAHOO_NIKKEI_PAGE_WITH_RELATED_KOSPI_HTML, now=NOW)
    kospi = parse_yahoo_quote_page("kospi", YAHOO_KOSPI_PAGE_WITH_RELATED_NIKKEI_HTML, now=NOW)

    assert nikkei is not None
    assert kospi is not None
    nikkei_payload = nikkei.to_dict()
    kospi_payload = kospi.to_dict()
    assert nikkei_payload["providerSymbol"] == "^N225"
    assert nikkei_payload["price"] == 38088.57
    assert nikkei_payload["changePercent"] == 0.30
    assert kospi_payload["providerSymbol"] == "^KS11"
    assert kospi_payload["price"] == 2920.03
    assert kospi_payload["changePercent"] == -0.29


def test_global_markets_fallback_parses_yahoo_quote_price_section() -> None:
    snapshot = parse_yahoo_quote_page("kospi", YAHOO_KOSPI_PAGE_WITH_QUOTE_PRICE_SECTION_HTML, now=NOW)

    assert snapshot is not None
    payload = snapshot.to_dict()
    assert payload["providerSymbol"] == "^KS11"
    assert payload["price"] == 8041.35
    assert payload["change"] == 556.94
    assert payload["changePercent"] == 7.44


def test_global_markets_fallback_parses_europe_india_brazil_quote_price_sections() -> None:
    cases = [
        ("ftse_100", YAHOO_FTSE_PAGE_HTML, "^FTSE", 8842.45, 0.60),
        ("dax", YAHOO_DAX_PAGE_HTML, "^GDAXI", 24155.50, -0.20),
        ("cac40", YAHOO_CAC40_PAGE_HTML, "^FCHI", 7729.32, 0.16),
        ("sensex", YAHOO_SENSEX_PAGE_HTML, "^BSESN", 82445.21, 0.23),
        ("ibovespa", YAHOO_IBOVESPA_PAGE_HTML, "^BVSP", 136102.80, -0.31),
    ]

    for market_id, html, provider_symbol, price, change_percent in cases:
        snapshot = parse_yahoo_quote_page(market_id, html, now=NOW)

        assert snapshot is not None
        payload = snapshot.to_dict()
        assert payload["id"] == market_id
        assert payload["providerSymbol"] == provider_symbol
        assert payload["price"] == price
        assert payload["changePercent"] == change_percent


def test_global_markets_parser_marks_old_quotes_stale_after_thirty_minutes() -> None:
    old_time = NOW - timedelta(minutes=31)
    fixture = {"symbol": "^DJI", "regularMarketPrice": 38600, "regularMarketTime": old_time.isoformat()}
    snapshot = parse_fallback_quote("dow_jones", fixture, now=NOW)

    assert snapshot is not None
    assert snapshot.stale is True
    assert is_snapshot_stale(NOW - timedelta(minutes=30), now=NOW) is False
    assert is_snapshot_stale(old_time, now=NOW) is True


def test_global_markets_parser_payload_is_json_serializable() -> None:
    snapshot = parse_fallback_quote("nasdaq_composite", YAHOO_QUOTE_FIXTURE, now=NOW)

    assert snapshot is not None
    assert json.loads(json.dumps(snapshot.to_dict()))["id"] == "nasdaq_composite"


def test_global_markets_fallback_merge_prefers_eastmoney_and_keeps_contract_order() -> None:
    dow = parse_eastmoney_quote("dow_jones", EASTMONEY_FIXTURE, now=NOW)
    sp500 = parse_fallback_quote("sp500", YAHOO_CHART_FIXTURE, now=NOW)
    nasdaq = parse_fallback_quote("nasdaq_composite", YAHOO_QUOTE_FIXTURE, now=NOW)

    merged = merge_provider_snapshots(
        {"dow_jones": dow, "sp500": None},
        {"dow_jones": nasdaq, "sp500": sp500, "nasdaq_composite": nasdaq},
    )

    assert [snapshot.id for snapshot in merged] == ["dow_jones", "sp500", "nasdaq_composite"]
    assert merged[0].source == "eastmoney"
    assert merged[2].provider_symbol == "^IXIC"


def test_global_markets_client_fetch_normalizes_all_regions_and_uses_fallback(monkeypatch) -> None:
    fresh_time = datetime.now(UTC)
    eastmoney_rows = [
        _eastmoney_row("DJIA", "100", 38662.25, 0.18, fresh_time),
        _eastmoney_row("SPX", "100", 5362.12, 0.22, fresh_time),
        _eastmoney_row("HSI", "100", 18420.1, -0.1, fresh_time),
        _eastmoney_row("000001", "1", 3048.5, 0.3, fresh_time),
        _eastmoney_row("399001", "0", 9550.2, -0.2, fresh_time),
        _eastmoney_row("000300", "1", 3520.3, 0.1, fresh_time),
    ]
    fallback_rows = [
        {
            "symbol": "^IXIC",
            "regularMarketPrice": 17133.13,
            "regularMarketChange": -14.81,
            "regularMarketChangePercent": -0.0864,
            "currency": "USD",
            "marketState": "REGULAR",
            "regularMarketTime": fresh_time.isoformat(),
            "source": "yahoo-finance-nasdaq-composite",
        },
        {
            "symbol": "^N225",
            "regularMarketPrice": 38088.57,
            "regularMarketChange": 112.25,
            "regularMarketChangePercent": 0.30,
            "currency": "JPY",
            "marketState": "REGULAR",
            "regularMarketTime": fresh_time.isoformat(),
            "source": "yahoo-finance",
        },
        {
            "symbol": "^KS11",
            "regularMarketPrice": 2920.03,
            "regularMarketChange": -8.54,
            "regularMarketChangePercent": -0.29,
            "currency": "KRW",
            "marketState": "REGULAR",
            "regularMarketTime": fresh_time.isoformat(),
            "source": "yahoo-finance",
        },
        _yahoo_quote_row("^FTSE", 8842.45, 52.33, 0.60, "GBP", fresh_time),
        _yahoo_quote_row("^GDAXI", 24155.50, -48.11, -0.20, "EUR", fresh_time),
        _yahoo_quote_row("^FCHI", 7729.32, 12.20, 0.16, "EUR", fresh_time),
        _yahoo_quote_row("IMOEX.ME", 2842.11, 18.45, 0.65, "RUB", fresh_time),
        _yahoo_quote_row("^BSESN", 82445.21, 190.12, 0.23, "INR", fresh_time),
        _yahoo_quote_row("^BVSP", 136102.80, -420.15, -0.31, "BRL", fresh_time),
    ]
    settings = _settings()
    client = GlobalMarketsClient(settings)
    monkeypatch.setattr(client, "_fetch_eastmoney_payload", lambda: {"data": {"diff": eastmoney_rows}})
    monkeypatch.setattr(client, "_fetch_fallback_payload", lambda market_ids: {"quoteResponse": {"result": fallback_rows}})

    payload = client.fetch()

    items = cast(list[dict[str, object]], payload["items"])
    regions = cast(list[dict[str, object]], payload["regions"])

    assert [item["id"] for item in items] == [
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
    ]
    assert [region["id"] for region in regions] == ["US", "HK", "CN", "JP", "KR", "GB", "DE", "FR", "RU", "IN", "BR"]
    assert items[2]["providerSymbol"] == "^IXIC"
    assert items[7]["providerSymbol"] == "^N225"
    assert items[8]["providerSymbol"] == "^KS11"
    assert items[9]["providerSymbol"] == "^FTSE"
    assert items[10]["providerSymbol"] == "^GDAXI"
    assert items[11]["providerSymbol"] == "^FCHI"
    assert items[12]["id"] == "moex_russia"
    assert items[12]["providerSymbol"] == "IMOEX.ME"
    assert items[12]["source"] == "yahoo-finance"
    assert items[12]["price"] == 2842.11
    assert items[13]["providerSymbol"] == "^BSESN"
    assert items[14]["providerSymbol"] == "^BVSP"
    assert regions[0]["marketIds"] == ["dow_jones", "sp500", "nasdaq_composite"]
    assert regions[3]["marketIds"] == ["nikkei_225"]
    assert regions[4]["marketIds"] == ["kospi"]
    assert regions[8]["marketIds"] == ["moex_russia"]
    assert len(items) == 15
    assert len(regions) == 11
    assert payload["stale"] is False
    assert json.loads(json.dumps(payload))["source"]


def test_global_markets_client_uses_nasdaq_page_when_quote_api_is_rate_limited(monkeypatch) -> None:
    fresh_time = datetime.now(UTC)
    eastmoney_rows = [
        _eastmoney_row("DJIA", "100", 38662.25, 0.18, fresh_time),
        _eastmoney_row("SPX", "100", 5362.12, 0.22, fresh_time),
        _eastmoney_row("HSI", "100", 18420.1, -0.1, fresh_time),
        _eastmoney_row("000001", "1", 3048.5, 0.3, fresh_time),
        _eastmoney_row("399001", "0", 9550.2, -0.2, fresh_time),
        _eastmoney_row("000300", "1", 3520.3, 0.1, fresh_time),
    ]
    settings = _settings()
    client = GlobalMarketsClient(settings)
    monkeypatch.setattr(client, "_fetch_eastmoney_payload", lambda: {"data": {"diff": eastmoney_rows}})
    monkeypatch.setattr(client, "_fetch_fallback_payload", lambda _market_ids: (_ for _ in ()).throw(RuntimeError("429 Too Many Requests")))
    monkeypatch.setattr(client, "_fetch_yahoo_quote_page", _yahoo_page_fixture)
    monkeypatch.setattr(client, "_fetch_moex_iss_payload", lambda: MOEX_ISS_FIXTURE)

    payload = client.fetch()
    items = cast(list[dict[str, object]], payload["items"])
    errors = cast(list[dict[str, object]], payload["errors"])
    nasdaq = items[2]
    nikkei = items[7]
    kospi = items[8]
    ftse = items[9]
    dax = items[10]
    cac40 = items[11]
    moex = items[12]
    sensex = items[13]
    ibovespa = items[14]

    assert len(items) == 15
    assert nasdaq["id"] == "nasdaq_composite"
    assert nasdaq["providerSymbol"] == "^IXIC"
    assert nasdaq["source"] == "yahoo-finance-quote-page-nasdaq-composite"
    assert nasdaq["price"] == 25929.66
    assert nasdaq["changePercent"] == 0.86
    assert nikkei["id"] == "nikkei_225"
    assert nikkei["providerSymbol"] == "^N225"
    assert nikkei["source"] == "yahoo-finance-quote-page-nikkei-225"
    assert kospi["id"] == "kospi"
    assert kospi["providerSymbol"] == "^KS11"
    assert kospi["source"] == "yahoo-finance-quote-page-kospi"
    assert ftse["id"] == "ftse_100"
    assert ftse["providerSymbol"] == "^FTSE"
    assert ftse["source"] == "yahoo-finance-quote-page-ftse-100"
    assert dax["id"] == "dax"
    assert dax["providerSymbol"] == "^GDAXI"
    assert cac40["id"] == "cac40"
    assert cac40["providerSymbol"] == "^FCHI"
    assert moex["id"] == "moex_russia"
    assert moex["providerSymbol"] == "IMOEX"
    assert moex["source"] == "moex-iss"
    assert moex["price"] == 2842.11
    assert sensex["id"] == "sensex"
    assert sensex["providerSymbol"] == "^BSESN"
    assert ibovespa["id"] == "ibovespa"
    assert ibovespa["providerSymbol"] == "^BVSP"
    assert {"source": "fallback", "marketId": None, "message": "429 Too Many Requests"} in errors


def test_global_markets_client_keeps_canonical_items_when_provider_missing(monkeypatch) -> None:
    fresh_time = datetime.now(UTC)
    eastmoney_rows = [
        _eastmoney_row("DJIA", "100", 38662.25, 0.18, fresh_time),
        _eastmoney_row("SPX", "100", 5362.12, 0.22, fresh_time),
        _eastmoney_row("HSI", "100", 18420.1, -0.1, fresh_time),
        _eastmoney_row("000001", "1", 3048.5, 0.3, fresh_time),
        _eastmoney_row("399001", "0", 9550.2, -0.2, fresh_time),
        _eastmoney_row("000300", "1", 3520.3, 0.1, fresh_time),
    ]
    settings = _settings()
    client = GlobalMarketsClient(settings)
    monkeypatch.setattr(client, "_fetch_eastmoney_payload", lambda: {"data": {"diff": eastmoney_rows}})
    monkeypatch.setattr(client, "_fetch_fallback_payload", lambda _market_ids: {"quoteResponse": {"result": []}})
    monkeypatch.setattr(client, "_fetch_yahoo_quote_page", lambda _provider_symbol: (_ for _ in ()).throw(RuntimeError("page unavailable")))
    monkeypatch.setattr(client, "_fetch_moex_iss_payload", lambda: MOEX_ISS_FIXTURE)

    payload = client.fetch()
    items = cast(list[dict[str, object]], payload["items"])
    nasdaq = items[2]

    assert len(items) == 15
    assert nasdaq["id"] == "nasdaq_composite"
    assert nasdaq["price"] is None
    assert nasdaq["stale"] is True
    assert nasdaq["source"] == "unavailable"
    assert items[7]["id"] == "nikkei_225"
    assert items[8]["id"] == "kospi"
    assert items[9]["id"] == "ftse_100"
    assert items[10]["id"] == "dax"
    assert items[11]["id"] == "cac40"
    assert items[12]["id"] == "moex_russia"
    assert items[13]["id"] == "sensex"
    assert items[14]["id"] == "ibovespa"
    assert items[7]["source"] == "unavailable"
    assert items[8]["source"] == "unavailable"
    assert items[9]["source"] == "unavailable"
    assert items[10]["source"] == "unavailable"
    assert items[11]["source"] == "unavailable"
    assert items[12]["source"] == "moex-iss"
    assert items[12]["providerSymbol"] == "IMOEX"
    assert items[12]["price"] == 2842.11
    assert items[13]["source"] == "unavailable"
    assert items[14]["source"] == "unavailable"
    assert payload["stale"] is True


def test_global_markets_cache_worker_writes_payload_with_ttl(monkeypatch) -> None:
    async def exercise() -> None:
        result = await worker.run(run_once=True)

        assert result is None
        assert redis.set_calls == [("moneyrush:global_markets:latest", json.dumps(payload), 120)]

    redis = FakeRedis()
    payload: dict[str, object] = {
        "items": [],
        "regions": [],
        "source": "test",
        "updatedAt": NOW.isoformat(),
        "delayLabel": None,
        "stale": False,
        "errors": [],
    }
    monkeypatch.setattr("collector.workers.global_markets_loop.Redis.from_url", lambda *_args, **_kwargs: redis)
    monkeypatch.setattr("collector.workers.global_markets_loop.GlobalMarketsClient", lambda _settings: FakeClient(payload))
    worker = GlobalMarketsCollectorWorker(_settings())

    asyncio.run(exercise())


def test_global_markets_last_good_cache_reused_as_stale_on_failure(monkeypatch) -> None:
    async def exercise() -> None:
        payload = await worker.refresh_once()
        items = cast(list[dict[str, object]], payload["items"])
        regions = cast(list[dict[str, object]], payload["regions"])
        errors = cast(list[dict[str, object]], payload["errors"])

        assert payload["stale"] is True
        assert items[0]["stale"] is True
        assert regions[0]["stale"] is True
        assert errors[-1]["source"] == "worker"
        assert redis.set_calls[0][2] == 120

    previous = {
        "items": [{"id": "dow_jones", "stale": False}],
        "regions": [{"id": "US", "stale": False}],
        "source": "eastmoney",
        "updatedAt": NOW.isoformat(),
        "delayLabel": "15分钟延迟",
        "stale": False,
        "errors": [],
    }
    redis = FakeRedis(json.dumps(previous))
    monkeypatch.setattr("collector.workers.global_markets_loop.Redis.from_url", lambda *_args, **_kwargs: redis)
    monkeypatch.setattr("collector.workers.global_markets_loop.GlobalMarketsClient", lambda _settings: FailingClient())
    worker = GlobalMarketsCollectorWorker(_settings())

    asyncio.run(exercise())


def test_global_markets_client_marks_previous_payload_items_stale(monkeypatch) -> None:
    settings = _settings(global_markets_stale_after_minutes=1)
    client = GlobalMarketsClient(settings)
    monkeypatch.setattr(client, "_fetch_eastmoney_payload", lambda: {"data": {"diff": []}})
    monkeypatch.setattr(client, "_fetch_fallback_payload", lambda _market_ids: {"quoteResponse": {"result": []}})
    monkeypatch.setattr(client, "_fetch_yahoo_quote_page", lambda _provider_symbol: (_ for _ in ()).throw(RuntimeError("page unavailable")))
    previous = {
        "items": [
            {
                "id": "dow_jones",
                "providerSymbol": "^DJI",
                "price": 38600,
                "updatedAt": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
                "source": "last-good",
            }
        ]
    }

    payload = client.fetch(previous)
    items = cast(list[dict[str, object]], payload["items"])

    assert items[0]["id"] == "dow_jones"
    assert items[0]["stale"] is True
    assert payload["stale"] is True


def _eastmoney_row(
    symbol: str,
    market: str,
    price: float,
    change_percent: float,
    updated_at: datetime,
) -> dict[str, object]:
    return {
        "f12": symbol,
        "f13": market,
        "f2": price,
        "f3": change_percent,
        "f4": price * change_percent / 100,
        "f124": updated_at.isoformat(),
        "marketStatus": "trading",
        "delayLabel": "15分钟延迟",
    }


def _yahoo_quote_row(
    symbol: str,
    price: float,
    change: float,
    change_percent: float,
    currency: str,
    updated_at: datetime,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "regularMarketPrice": price,
        "regularMarketChange": change,
        "regularMarketChangePercent": change_percent,
        "currency": currency,
        "marketState": "REGULAR",
        "regularMarketTime": updated_at.isoformat(),
        "source": "yahoo-finance",
    }


def _yahoo_page_fixture(provider_symbol: str) -> str:
    return {
        "^IXIC": YAHOO_NASDAQ_PAGE_HTML,
        "^N225": YAHOO_NIKKEI_PAGE_HTML,
        "^KS11": YAHOO_KOSPI_PAGE_HTML,
        "^FTSE": YAHOO_FTSE_PAGE_HTML,
        "^GDAXI": YAHOO_DAX_PAGE_HTML,
        "^FCHI": YAHOO_CAC40_PAGE_HTML,
        "^BSESN": YAHOO_SENSEX_PAGE_HTML,
        "^BVSP": YAHOO_IBOVESPA_PAGE_HTML,
    }[provider_symbol]


def _settings(**overrides):
    defaults = {
        "redis_url": "redis://example/0",
        "global_markets_collector_enabled": True,
        "global_markets_refresh_seconds": 30,
        "global_markets_cache_key": "moneyrush:global_markets:latest",
        "global_markets_request_timeout_seconds": 10.0,
        "global_markets_stale_after_minutes": 30,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def fetch(self, _previous_payload=None) -> dict[str, object]:
        return self._payload


class FailingClient:
    def fetch(self, _previous_payload=None) -> dict[str, object]:
        raise RuntimeError("provider timeout")


class FakeRedis:
    def __init__(self, cached_payload: str | None = None) -> None:
        self._cached_payload = cached_payload
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, _key: str) -> str | None:
        return self._cached_payload

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls.append((key, value, ex))
        self._cached_payload = value
