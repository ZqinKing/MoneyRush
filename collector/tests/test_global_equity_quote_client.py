from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import pytest

from collector.services.global_equity_quote_client import (
    GlobalEquityQuoteClient,
    GlobalEquityQuoteClientError,
    normalize_global_equity_symbol,
)


def test_normalizes_us_and_hk_symbols() -> None:
    assert normalize_global_equity_symbol("AAPL").canonical == "AAPL.US"
    assert normalize_global_equity_symbol("AAPL").eastmoney_secid == "105.AAPL"
    assert normalize_global_equity_symbol("AAPL").sina_symbol == "gb_aapl"
    assert normalize_global_equity_symbol("AAPL.US").yahoo_symbol == "AAPL"
    assert normalize_global_equity_symbol("700", market="HK").canonical == "00700.HK"
    assert normalize_global_equity_symbol("700", market="HK").eastmoney_secid == "116.00700"
    assert normalize_global_equity_symbol("700", market="HK").sina_symbol == "rt_hk00700"
    assert normalize_global_equity_symbol("HK00700").yahoo_symbol == "0700.HK"


def test_fetches_eastmoney_delay_us_as_market_state() -> None:
    session = FakeSession([
        FakeResponse(
            json_payload={
                "data": {
                    "diff": [
                        {
                            "f12": "AAPL",
                            "f14": "苹果",
                            "f2": 308630,
                            "f3": 484,
                            "f4": 14250,
                            "f5": 75400626,
                            "f6": 23106283520.0,
                            "f15": 309420,
                            "f16": 293680,
                            "f17": 294120,
                            "f18": 294380,
                            "f20": 4532958682280,
                            "f124": 1783022400,
                        }
                    ]
                }
            }
        )
    ])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    state = client.fetch_quote("AAPL.US")

    assert session.calls[0]["url"] == "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
    assert cast(dict[str, object], session.calls[0]["params"])["secids"] == "105.AAPL"
    assert state["snapshot"]["symbol"] == "AAPL.US"
    assert state["snapshot"]["source"] == "eastmoney-delay"
    assert state["snapshot"]["lastPrice"] == 308.63
    assert state["snapshot"]["changePct"] == 4.84
    assert state["kline"]["period"] == "1d"
    assert cast(dict[str, object], state["kline"]["raw"])["synthetic"] is False
    json.dumps(state["event"])


def test_fetches_eastmoney_delay_hk_with_canonical_snapshot_symbol() -> None:
    session = FakeSession([
        FakeResponse(
            json_payload={
                "data": {
                    "diff": [
                        {
                            "f12": "00700",
                            "f14": "腾讯控股",
                            "f2": 432200,
                            "f3": 46,
                            "f4": 2000,
                            "f5": 21486249,
                            "f15": 445800,
                            "f16": 431200,
                            "f17": 433000,
                            "f18": 430200,
                            "f20": 3929663898280,
                            "f124": 1783063968,
                        }
                    ]
                }
            }
        )
    ])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    state = client.fetch_quote("00700.HK")

    assert cast(dict[str, object], session.calls[0]["params"])["secids"] == "116.00700"
    assert state["snapshot"]["symbol"] == "00700.HK"
    assert state["snapshot"]["market"] == "HK"
    assert state["snapshot"]["lastPrice"] == 432.2
    assert state["snapshot"]["currency"] == "HKD"


def test_falls_back_to_sina_us_when_eastmoney_is_empty() -> None:
    session = FakeSession([
        FakeResponse(json_payload={"data": {"diff": []}}),
        FakeResponse(text='var hq_str_gb_glw="康宁,196.7900,-10.81,2026-07-03 09:46:27,-23.8400,223.8850,224.0100,193.5400,271.7800,50.2000,21192219,23015507,169364917384,2.12,92.830000";'),
    ])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    state = client.fetch_quote("GLW.US")

    assert session.calls[1]["params"] == {"list": "gb_glw"}
    assert state["snapshot"]["source"] == "sina-finance"
    assert state["snapshot"]["lastPrice"] == 196.79
    assert state["snapshot"]["changePct"] == -10.81
    assert state["snapshot"]["currency"] == "USD"
    assert state["snapshot"]["updatedAt"] == "2026-07-03T13:46:27+00:00"


def test_falls_back_to_sina_hk_when_eastmoney_is_empty() -> None:
    session = FakeSession([
        FakeResponse(json_payload={"data": {"diff": []}}),
        FakeResponse(text='var hq_str_rt_hk00700="TENCENT,腾讯控股,433.000,430.200,445.800,431.200,432.400,2.200,0.511,432.200,432.400,9699106744.110,22180745,15.718,0.000,675.134,411.000,2026/07/03,15:43:50,30|3,N|Y|Y";'),
    ])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    state = client.fetch_quote("00700.HK")

    assert session.calls[1]["params"] == {"list": "rt_hk00700"}
    assert state["snapshot"]["source"] == "sina-finance"
    assert state["snapshot"]["lastPrice"] == 432.4
    assert state["snapshot"]["changePct"] == 0.511
    assert state["snapshot"]["currency"] == "HKD"
    assert state["snapshot"]["updatedAt"] == "2026-07-03T07:43:50+00:00"


def test_falls_back_to_yahoo_chart_when_eastmoney_and_sina_are_empty() -> None:
    session = FakeSession([
        FakeResponse(json_payload={"data": {"diff": []}}),
        FakeResponse(text='var hq_str_gb_aapl="";'),
        FakeResponse(
            json_payload={
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "symbol": "AAPL",
                                "currency": "USD",
                                "fullExchangeName": "NasdaqGS",
                                "regularMarketTime": 1780910700,
                                "previousClose": 210.0,
                            },
                            "timestamp": [1780824300, 1780910700],
                            "indicators": {"quote": [{"open": [209.0, 210.0], "high": [211.0, 213.0], "low": [208.5, 209.5], "close": [210.0, 212.4], "volume": [100, 123456]}]},
                        }
                    ]
                }
            }
        ),
    ])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    state = client.fetch_quote("AAPL.US")

    assert session.calls[2]["url"] == "https://query2.finance.yahoo.com/v8/finance/chart/AAPL"
    assert state["snapshot"]["source"] == "yahoo-finance-chart"
    assert round(cast(float, state["snapshot"]["changePct"]), 4) == 1.1429


def test_falls_back_to_yahoo_quote_when_chart_endpoint_is_empty() -> None:
    session = FakeSession([
        FakeResponse(json_payload={"data": {"diff": []}}),
        FakeResponse(text='var hq_str_gb_aapl="";'),
        FakeResponse(json_payload={"chart": {"result": []}}),
        FakeResponse(
            json_payload={
                "quoteResponse": {
                    "result": [
                        {
                            "symbol": "AAPL",
                            "shortName": "Apple Inc.",
                            "regularMarketPrice": 212.4,
                            "regularMarketChangePercent": 1.25,
                            "regularMarketTime": 1780910700,
                            "regularMarketOpen": 210.0,
                            "regularMarketDayHigh": 213.0,
                            "regularMarketDayLow": 209.5,
                            "regularMarketVolume": 123456,
                            "fullExchangeName": "NasdaqGS",
                            "currency": "USD",
                        }
                    ]
                }
            }
        ),
    ])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    state = client.fetch_quote("AAPL.US")

    assert session.calls[3]["params"] == {"symbols": "AAPL"}
    assert state["snapshot"]["source"] == "yahoo-finance"
    assert state["snapshot"]["companyName"] == "Apple Inc."
    assert state["snapshot"]["changePct"] == 1.25
    assert state["kline"]["high"] == 213.0


def test_falls_back_to_stooq_for_us_when_yahoo_unavailable() -> None:
    session = FakeSession([
        FakeResponse(json_payload={"data": {"diff": []}}),
        FakeResponse(text='var hq_str_gb_aapl="";'),
        FakeResponse(json_payload={"chart": {"result": []}}),
        FakeResponse(status_error=RuntimeError("rate limited")),
        FakeResponse(text="Symbol,Date,Time,Open,High,Low,Close,Volume,Name\nAAPL.US,2026-06-08,20:59:59,210,213,209,212.4,123456,Apple Inc.\n"),
    ])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    state = client.fetch_quote("AAPL")

    assert cast(dict[str, object], session.calls[4]["params"])["s"] == "aapl.us"
    assert state["snapshot"]["source"] == "stooq-eod"
    assert state["snapshot"]["delayLabel"] == "EOD/delayed"
    assert state["snapshot"]["changePct"] is None


def test_rejects_bad_symbol_before_network_call() -> None:
    session = FakeSession([])
    client = GlobalEquityQuoteClient(_settings(), session=session)

    with pytest.raises(GlobalEquityQuoteClientError):
        client.fetch_quote("!!!")
    assert session.calls == []


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        overseas_equity_request_timeout_seconds=10.0,
        overseas_equity_eastmoney_min_interval_seconds=0.0,
        overseas_equity_sina_min_interval_seconds=0.0,
        overseas_equity_yahoo_min_interval_seconds=0.0,
        overseas_equity_stooq_min_interval_seconds=0.0,
    )


class FakeResponse:
    def __init__(self, *, json_payload: dict[str, object] | None = None, text: str = "", status_error: Exception | None = None) -> None:
        self._json_payload = json_payload
        self.text = text
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> dict[str, object]:
        return self._json_payload or {}


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError("unexpected HTTP call")
        return self._responses.pop(0)
