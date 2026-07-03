from __future__ import annotations

import csv
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from math import isfinite
from typing import Protocol, cast
from zoneinfo import ZoneInfo

import requests


logger = logging.getLogger(__name__)

YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
EASTMONEY_DELAY_QUOTE_URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
SINA_QUOTE_URL = "https://hq.sinajs.cn/"
STOOQ_QUOTE_URL = "https://stooq.com/q/l/"
QUOTE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json,text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
US_MARKET_TZ = ZoneInfo("America/New_York")
HK_MARKET_TZ = ZoneInfo("Asia/Hong_Kong")


class GlobalEquityQuoteClientError(ValueError):
    pass


class HttpResponse(Protocol):
    text: str

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class HttpSession(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class GlobalEquitySymbol:
    canonical: str
    market: str
    yahoo_symbol: str
    eastmoney_secid: str
    sina_symbol: str
    stooq_symbol: str | None


class GlobalEquityQuoteClient:
    def __init__(self, settings, *, session: HttpSession | None = None) -> None:
        self._settings = settings
        self._session: HttpSession = session if session is not None else cast(HttpSession, cast(object, requests.Session()))
        self._timeout = float(settings.overseas_equity_request_timeout_seconds)
        self._last_request_at: dict[str, float] = {}

    def fetch_quote(self, symbol: str, *, market: str | None = None, name: str | None = None) -> dict[str, dict[str, object]]:
        normalized = normalize_global_equity_symbol(symbol, market=market)
        errors: list[str] = []
        try:
            row = self._fetch_eastmoney_delay_quote(normalized)
            return self._market_state_from_row(normalized, row, source="eastmoney-delay", fallback_name=name)
        except Exception as exc:
            errors.append(f"eastmoney-delay:{exc}")
            logger.info("eastmoney overseas equity quote failed", extra={"symbol": normalized.canonical, "error": str(exc)})

        try:
            row = self._fetch_sina_quote(normalized)
            return self._market_state_from_row(normalized, row, source="sina-finance", fallback_name=name)
        except Exception as exc:
            errors.append(f"sina-finance:{exc}")
            logger.info("sina overseas equity quote failed", extra={"symbol": normalized.canonical, "error": str(exc)})

        try:
            row = self._fetch_yahoo_chart(normalized.yahoo_symbol)
            return self._market_state_from_row(normalized, row, source="yahoo-finance-chart", fallback_name=name)
        except Exception as exc:
            errors.append(f"yahoo-finance-chart:{exc}")
            logger.info("yahoo overseas equity chart failed", extra={"symbol": normalized.canonical, "error": str(exc)})

        try:
            row = self._fetch_yahoo_quote(normalized.yahoo_symbol)
            return self._market_state_from_row(normalized, row, source="yahoo-finance", fallback_name=name)
        except Exception as exc:
            errors.append(f"yahoo-finance:{exc}")
            logger.info("yahoo overseas equity quote failed", extra={"symbol": normalized.canonical, "error": str(exc)})

        if normalized.market == "US" and normalized.stooq_symbol:
            try:
                row = self._fetch_stooq_quote(normalized.stooq_symbol)
                return self._market_state_from_row(normalized, row, source="stooq-eod", fallback_name=name)
            except Exception as exc:
                errors.append(f"stooq-eod:{exc}")
                logger.info("stooq overseas equity quote failed", extra={"symbol": normalized.canonical, "error": str(exc)})

        raise GlobalEquityQuoteClientError(f"no usable overseas equity quote for {normalized.canonical}: {'; '.join(errors)}")

    def _fetch_eastmoney_delay_quote(self, symbol: GlobalEquitySymbol) -> dict[str, object]:
        self._wait_for_provider("eastmoney-delay", self._settings.overseas_equity_eastmoney_min_interval_seconds)
        response = self._session.get(
            EASTMONEY_DELAY_QUOTE_URL,
            params={
                "secids": symbol.eastmoney_secid,
                "fields": "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f20,f21,f44,f45,f46,f58,f107,f124,f152",
            },
            headers={**QUOTE_HEADERS, "Referer": "https://quote.eastmoney.com/"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GlobalEquityQuoteClientError("invalid eastmoney payload")
        data = _as_dict(payload.get("data"))
        rows = data.get("diff") if data is not None else None
        if not isinstance(rows, list) or not rows:
            raise GlobalEquityQuoteClientError("empty eastmoney result")
        row = next((item for item in rows if isinstance(item, dict) and _text(item.get("f12")) == symbol.canonical.rsplit(".", 1)[0]), rows[0])
        if not isinstance(row, dict):
            raise GlobalEquityQuoteClientError("invalid eastmoney row")
        return _normalize_eastmoney_delay_row(cast(dict[str, object], row), symbol)

    def _fetch_sina_quote(self, symbol: GlobalEquitySymbol) -> dict[str, object]:
        self._wait_for_provider("sina-finance", self._settings.overseas_equity_sina_min_interval_seconds)
        response = self._session.get(
            SINA_QUOTE_URL,
            params={"list": symbol.sina_symbol},
            headers={**QUOTE_HEADERS, "Referer": "https://finance.sina.com.cn/"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return _normalize_sina_quote_row(response.text, symbol)

    def _fetch_yahoo_quote(self, provider_symbol: str) -> dict[str, object]:
        self._wait_for_provider("yahoo-finance", self._settings.overseas_equity_yahoo_min_interval_seconds)
        response = self._session.get(
            YAHOO_QUOTE_URL,
            params={"symbols": provider_symbol},
            headers=QUOTE_HEADERS,
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GlobalEquityQuoteClientError("invalid yahoo payload")
        quote_response = payload.get("quoteResponse")
        if not isinstance(quote_response, dict):
            raise GlobalEquityQuoteClientError("missing yahoo quoteResponse")
        rows = quote_response.get("result")
        if not isinstance(rows, list) or not rows:
            raise GlobalEquityQuoteClientError("empty yahoo result")
        for row in rows:
            if not isinstance(row, dict):
                continue
            if _text(row.get("symbol")) == provider_symbol:
                return cast(dict[str, object], row)
        first_row = rows[0]
        if not isinstance(first_row, dict):
            raise GlobalEquityQuoteClientError("invalid yahoo result row")
        return cast(dict[str, object], first_row)

    def _fetch_yahoo_chart(self, provider_symbol: str) -> dict[str, object]:
        self._wait_for_provider("yahoo-finance", self._settings.overseas_equity_yahoo_min_interval_seconds)
        response = self._session.get(
            YAHOO_CHART_URL.format(symbol=provider_symbol),
            params={"range": "5d", "interval": "1d"},
            headers=QUOTE_HEADERS,
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GlobalEquityQuoteClientError("invalid yahoo chart payload")
        chart = _as_dict(payload.get("chart"))
        rows = chart.get("result") if chart is not None else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise GlobalEquityQuoteClientError("empty yahoo chart result")
        return _normalize_yahoo_chart_row(cast(dict[str, object], rows[0]), provider_symbol)

    def _fetch_stooq_quote(self, provider_symbol: str) -> dict[str, object]:
        self._wait_for_provider("stooq-eod", self._settings.overseas_equity_stooq_min_interval_seconds)
        response = self._session.get(
            STOOQ_QUOTE_URL,
            params={"s": provider_symbol, "f": "sd2t2ohlcvn", "h": "", "e": "csv"},
            headers=QUOTE_HEADERS,
            timeout=self._timeout,
        )
        response.raise_for_status()
        if response.text.lstrip().startswith("<"):
            raise GlobalEquityQuoteClientError("stooq returned html challenge")
        rows = list(csv.DictReader(StringIO(response.text.strip())))
        if not rows:
            raise GlobalEquityQuoteClientError("empty stooq result")
        row = rows[0]
        if _text(row.get("Close")) in {None, "N/D"}:
            raise GlobalEquityQuoteClientError("stooq returned no close")
        return dict(row)

    def _wait_for_provider(self, provider: str, min_interval_seconds: float) -> None:
        interval = max(float(min_interval_seconds), 0.0)
        previous = self._last_request_at.get(provider)
        now = time.monotonic()
        if previous is not None and now - previous < interval:
            time.sleep(interval - (now - previous))
        self._last_request_at[provider] = time.monotonic()

    def _market_state_from_row(
        self,
        symbol: GlobalEquitySymbol,
        row: dict[str, object],
        *,
        source: str,
        fallback_name: str | None,
    ) -> dict[str, dict[str, object]]:
        price = _positive_float(_pick(row, "regularMarketPrice", "Close", "close"), field_name="price")
        updated_at = _row_timestamp(row, symbol.market)
        company_name = _text(_pick(row, "shortName", "longName", "Name", "name")) or fallback_name or symbol.canonical
        change_pct = _change_pct(row)
        open_price = _to_float(_pick(row, "regularMarketOpen", "Open", "open"))
        high = _to_float(_pick(row, "regularMarketDayHigh", "High", "high"))
        low = _to_float(_pick(row, "regularMarketDayLow", "Low", "low"))
        close = price
        synthetic_bar = False
        if open_price is None or high is None or low is None:
            open_price = high = low = close
            synthetic_bar = True
        elif not _valid_ohlc(open_price, high, low, close):
            raise GlobalEquityQuoteClientError("invalid OHLC quote row")

        volume = _to_int(_pick(row, "regularMarketVolume", "Volume", "volume"))
        amount = price * volume if volume is not None else None
        delay_label = "EOD/delayed" if source == "stooq-eod" else _text(row.get("delayLabel"))
        raw: dict[str, object] = {"provider": source, "synthetic": synthetic_bar, "payload": row}
        snapshot: dict[str, object] = {
            "symbol": symbol.canonical,
            "companyName": company_name,
            "exchange": _text(_pick(row, "fullExchangeName", "exchange", "exchangeName")) or symbol.market,
            "lastPrice": price,
            "changePct": change_pct,
            "pe": _to_float(_pick(row, "trailingPE", "pe")),
            "pb": _to_float(_pick(row, "priceToBook", "pb")),
            "turnoverRate": None,
            "marketCap": _to_float(_pick(row, "marketCap", "market_cap")),
            "limitUp": None,
            "limitDown": None,
            "source": source,
            "updatedAt": updated_at.isoformat(),
            "market": symbol.market,
            "currency": _text(_pick(row, "currency", "Currency")),
            "delayLabel": delay_label,
            "raw": raw,
        }
        tick: dict[str, object] = {
            "ts": updated_at,
            "symbol": symbol.canonical,
            "price": price,
            "volume": volume,
            "amount": amount,
            "side": None,
            "source": source,
            "raw": raw,
        }
        kline: dict[str, object] = {
            "bucketTs": updated_at,
            "symbol": symbol.canonical,
            "period": "1d",
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
            "source": source,
            "raw": raw,
        }
        event_tick = {**tick, "ts": updated_at.isoformat()}
        event_kline = {**kline, "bucketTs": updated_at.isoformat()}
        event: dict[str, object] = {
            "symbol": symbol.canonical,
            "type": "quote",
            "severity": "info",
            "message": f"{symbol.canonical} quote refreshed from {source}",
            "snapshot": snapshot,
            "tick": event_tick,
            "kline": event_kline,
        }
        return {"snapshot": snapshot, "tick": tick, "kline": kline, "event": event}


def normalize_global_equity_symbol(symbol: str, *, market: str | None = None) -> GlobalEquitySymbol:
    text = re.sub(r"\s+", "", str(symbol or "")).upper()
    if not text:
        raise GlobalEquityQuoteClientError("empty overseas equity symbol")
    market_text = _text(market)
    market_text = market_text.upper() if market_text else None

    if "." in text:
        base, suffix = text.rsplit(".", 1)
        if suffix == "US" and base:
            return _us_symbol(base)
        if suffix == "HK" and base:
            return _hk_symbol(base)

    if text.startswith("HK") and text[2:].isdigit():
        return _hk_symbol(text[2:])
    if market_text == "HK" and text.isdigit():
        return _hk_symbol(text)
    if text.isdigit() and len(text) < 6:
        return _hk_symbol(text)

    if text.startswith("US") and len(text) > 2 and re.fullmatch(r"[A-Z0-9.-]+", text[2:]):
        return _us_symbol(text[2:])
    if market_text == "US" and re.fullmatch(r"[A-Z0-9.-]+", text):
        return _us_symbol(text)
    if re.fullmatch(r"[A-Z]+", text):
        return _us_symbol(text)

    raise GlobalEquityQuoteClientError(f"unsupported overseas equity symbol: {symbol}")


def _us_symbol(base: str) -> GlobalEquitySymbol:
    normalized = base.upper()
    yahoo_symbol = normalized.replace(".", "-")
    return GlobalEquitySymbol(
        canonical=f"{normalized}.US",
        market="US",
        yahoo_symbol=yahoo_symbol,
        eastmoney_secid=f"105.{normalized}",
        sina_symbol=f"gb_{normalized.lower()}",
        stooq_symbol=f"{normalized.lower()}.us",
    )


def _hk_symbol(base: str) -> GlobalEquitySymbol:
    digits = "".join(character for character in base if character.isdigit())
    if not digits:
        raise GlobalEquityQuoteClientError(f"unsupported HK equity symbol: {base}")
    normalized = f"{int(digits):05d}"
    return GlobalEquitySymbol(
        canonical=f"{normalized}.HK",
        market="HK",
        yahoo_symbol=f"{normalized[-4:]}.HK",
        eastmoney_secid=f"116.{normalized}",
        sina_symbol=f"rt_hk{normalized}",
        stooq_symbol=None,
    )


def _pick(row: dict[str, object], *keys: str) -> object:
    for key in keys:
        value = row.get(key)
        if value is not None and _text(value) not in {None, "", "N/D", "--"}:
            return value
    return None


def _normalize_yahoo_chart_row(row: dict[str, object], provider_symbol: str) -> dict[str, object]:
    meta = _as_dict(row.get("meta")) or {}
    indicators = _as_dict(row.get("indicators")) or {}
    quote_rows = indicators.get("quote")
    quotes = cast(list[object], quote_rows) if isinstance(quote_rows, list) else []
    quote = _as_dict(quotes[0]) if quotes else {}
    timestamps = _as_list(row.get("timestamp"))
    close_values = _as_list(quote.get("close") if quote is not None else None)
    index = _last_numeric_index(close_values)
    close = _to_float(_value_at(close_values, index)) if index is not None else _to_float(meta.get("regularMarketPrice"))
    if close is None:
        raise GlobalEquityQuoteClientError("yahoo chart missing close")
    previous_close = _to_float(meta.get("previousClose"))
    change = close - previous_close if previous_close is not None else None
    return {
        "symbol": meta.get("symbol") or provider_symbol,
        "shortName": meta.get("shortName") or meta.get("longName"),
        "regularMarketPrice": close,
        "regularMarketChange": change,
        "regularMarketChangePercent": (change / previous_close * 100) if change is not None and previous_close not in (None, 0) else None,
        "regularMarketTime": meta.get("regularMarketTime") or _value_at(timestamps, index),
        "regularMarketOpen": _value_at(_as_list(quote.get("open") if quote is not None else None), index),
        "regularMarketDayHigh": _value_at(_as_list(quote.get("high") if quote is not None else None), index),
        "regularMarketDayLow": _value_at(_as_list(quote.get("low") if quote is not None else None), index),
        "regularMarketVolume": _value_at(_as_list(quote.get("volume") if quote is not None else None), index),
        "currency": meta.get("currency"),
        "fullExchangeName": meta.get("fullExchangeName") or meta.get("exchangeName"),
    }


def _normalize_eastmoney_delay_row(row: dict[str, object], symbol: GlobalEquitySymbol) -> dict[str, object]:
    return {
        "symbol": row.get("f12") or symbol.canonical,
        "shortName": row.get("f14"),
        "regularMarketPrice": _eastmoney_price(row.get("f2")),
        "regularMarketChangePercent": _eastmoney_percent(row.get("f3")),
        "regularMarketChange": _eastmoney_price(row.get("f4")),
        "regularMarketTime": row.get("f124"),
        "regularMarketOpen": _eastmoney_price(row.get("f17")),
        "regularMarketDayHigh": _eastmoney_price(row.get("f15")),
        "regularMarketDayLow": _eastmoney_price(row.get("f16")),
        "regularMarketPreviousClose": _eastmoney_price(row.get("f18")),
        "regularMarketVolume": _to_int(row.get("f5")),
        "marketCap": _to_float(row.get("f20")),
        "currency": "USD" if symbol.market == "US" else "HKD",
        "fullExchangeName": "Eastmoney US" if symbol.market == "US" else "Eastmoney HK",
        "delayLabel": "Eastmoney delayed",
    }


def _normalize_sina_quote_row(text: str, symbol: GlobalEquitySymbol) -> dict[str, object]:
    match = re.search(r'="(.*?)";', text, flags=re.DOTALL)
    if match is None:
        raise GlobalEquityQuoteClientError("invalid sina payload")
    fields = [field.strip() for field in match.group(1).split(",")]
    return _normalize_sina_us_row(fields, symbol) if symbol.market == "US" else _normalize_sina_hk_row(fields, symbol)


def _normalize_sina_us_row(fields: list[str], symbol: GlobalEquitySymbol) -> dict[str, object]:
    date_text, time_text = _split_sina_datetime(_sina_field(fields, 3))
    return {
        "symbol": symbol.yahoo_symbol,
        "shortName": _sina_field(fields, 0),
        "regularMarketPrice": _sina_field(fields, 1),
        "regularMarketChangePercent": _sina_field(fields, 2),
        "Date": date_text,
        "Time": time_text,
        "regularMarketChange": _sina_field(fields, 4),
        "regularMarketOpen": _sina_field(fields, 5),
        "regularMarketDayHigh": _sina_field(fields, 6),
        "regularMarketDayLow": _sina_field(fields, 7),
        "regularMarketPreviousClose": _sina_field(fields, 36) or _sina_field(fields, 24),
        "regularMarketVolume": _sina_field(fields, 10),
        "marketCap": _sina_field(fields, 12),
        "trailingPE": _sina_field(fields, 14),
        "currency": "USD",
        "fullExchangeName": "Sina US",
        "delayLabel": "Sina delayed",
    }


def _normalize_sina_hk_row(fields: list[str], symbol: GlobalEquitySymbol) -> dict[str, object]:
    return {
        "symbol": symbol.canonical,
        "shortName": _sina_field(fields, 1) or _sina_field(fields, 0),
        "regularMarketPrice": _sina_field(fields, 6),
        "regularMarketChangePercent": _sina_field(fields, 8),
        "Date": (_sina_field(fields, 17) or "").replace("/", "-") or None,
        "Time": _sina_field(fields, 18),
        "regularMarketChange": _sina_field(fields, 7),
        "regularMarketOpen": _sina_field(fields, 2),
        "regularMarketDayHigh": _sina_field(fields, 4),
        "regularMarketDayLow": _sina_field(fields, 5),
        "regularMarketPreviousClose": _sina_field(fields, 3),
        "regularMarketVolume": _sina_field(fields, 12),
        "trailingPE": _sina_field(fields, 13),
        "currency": "HKD",
        "fullExchangeName": "Sina HK",
        "delayLabel": "Sina delayed",
    }


def _sina_field(fields: list[str], index: int) -> str | None:
    if index >= len(fields):
        return None
    return _text(fields[index])


def _split_sina_datetime(value: object) -> tuple[str | None, str | None]:
    text = _text(value)
    if text is None:
        return None, None
    parts = text.replace("/", "-").split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return parts[0], None


def _eastmoney_price(value: object) -> float | None:
    number = _to_float(value)
    return number / 1000 if number is not None else None


def _eastmoney_percent(value: object) -> float | None:
    number = _to_float(value)
    return number / 100 if number is not None else None


def _as_dict(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _last_numeric_index(values: list[object]) -> int | None:
    for index in range(len(values) - 1, -1, -1):
        if _to_float(values[index]) is not None:
            return index
    return None


def _value_at(values: list[object], index: int | None) -> object:
    if index is None or index < 0 or index >= len(values):
        return None
    return values[index]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat", "null"}:
        return None
    return text


def _to_float(value: object) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        number = float(text.replace(",", "").replace("%", ""))
    except ValueError:
        return None
    return number if isfinite(number) else None


def _positive_float(value: object, *, field_name: str) -> float:
    number = _to_float(value)
    if number is None or number <= 0:
        raise GlobalEquityQuoteClientError(f"invalid {field_name}")
    return number


def _to_int(value: object) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _change_pct(row: dict[str, object]) -> float | None:
    direct = _to_float(_pick(row, "regularMarketChangePercent", "changePct", "ChangePct"))
    if direct is not None:
        return direct
    change = _to_float(_pick(row, "regularMarketChange", "Change"))
    previous_close = _to_float(_pick(row, "regularMarketPreviousClose", "previousClose"))
    if change is None or previous_close in (None, 0):
        return None
    return change / previous_close * 100


def _market_timezone(market: str) -> ZoneInfo:
    return HK_MARKET_TZ if market == "HK" else US_MARKET_TZ


def _row_timestamp(row: dict[str, object], market: str = "US") -> datetime:
    for key in ("regularMarketTime", "postMarketTime", "preMarketTime"):
        value = _to_float(row.get(key))
        if value is not None and value > 0:
            return datetime.fromtimestamp(value, tz=UTC)
    date_text = _text(row.get("Date"))
    time_text = _text(row.get("Time"))
    if date_text:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                value = f"{date_text} {time_text}" if time_text and " " in pattern else date_text
                return datetime.strptime(value, pattern).replace(tzinfo=_market_timezone(market)).astimezone(UTC)
            except ValueError:
                continue
    return datetime.now(UTC)


def _valid_ohlc(open_price: float, high: float, low: float, close: float) -> bool:
    return all(value > 0 for value in (open_price, high, low, close)) and high >= low and high >= open_price and high >= close and low <= open_price and low <= close
