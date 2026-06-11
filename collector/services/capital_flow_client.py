from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import akshare as ak
import pandas as pd
from urllib.error import HTTPError, URLError


logger = logging.getLogger(__name__)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    "Accept": "application/json,text/plain,*/*",
}
MARKET_PREFIX_MAP = {"sh": "1", "sz": "0", "bj": "0"}


class CapitalFlowClientError(RuntimeError):
    pass


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _to_trade_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        text = value.strip()
        for parser in (datetime.fromisoformat,):
            try:
                return parser(text).date()
            except ValueError:
                continue
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _infer_market(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "sh"
    if symbol.startswith(("4", "8")):
        return "bj"
    return "sz"


class CapitalFlowClient:
    def __init__(
        self,
        *,
        eastmoney_base_url: str,
        eastmoney_delay_base_url: str | None = None,
        timeout_seconds: float = 15.0,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 0.8,
        akshare_fallback_enabled: bool = True,
    ) -> None:
        self._eastmoney_base_url = eastmoney_base_url
        self._eastmoney_delay_base_url = eastmoney_delay_base_url
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = max(int(retry_attempts), 1)
        self._retry_backoff_seconds = max(float(retry_backoff_seconds), 0.0)
        self._akshare_fallback_enabled = akshare_fallback_enabled

    def fetch_latest(self, symbol: str) -> dict[str, object]:
        return self._fetch_with_fallback(symbol=symbol, trade_date=None)

    def fetch_for_trade_date(self, symbol: str, trade_date: date) -> dict[str, object]:
        return self._fetch_with_fallback(symbol=symbol, trade_date=trade_date)

    def _fetch_with_fallback(self, *, symbol: str, trade_date: date | None) -> dict[str, object]:
        direct_error: Exception | None = None
        try:
            return self._fetch_latest_direct(symbol) if trade_date is None else self._fetch_direct_for_trade_date(symbol, trade_date)
        except Exception as exc:  # noqa: BLE001 - collector must degrade gracefully
            direct_error = exc
            logger.warning("capital flow direct fetch failed; trying akshare fallback", extra={"symbol": symbol, "error": str(exc)})

        if not self._akshare_fallback_enabled:
            raise CapitalFlowClientError(f"direct source failed: {direct_error}; akshare fallback disabled") from direct_error

        try:
            return self._fetch_latest_akshare(symbol) if trade_date is None else self._fetch_akshare_for_trade_date(symbol, trade_date)
        except Exception as exc:  # noqa: BLE001 - collector must report combined failure
            message = f"direct source failed: {direct_error}; akshare fallback failed: {exc}"
            raise CapitalFlowClientError(message) from exc

    def _fetch_latest_direct(self, symbol: str) -> dict[str, object]:
        market = _infer_market(symbol)
        payload, source = self._request_json(symbol=symbol, market=market)
        data = payload.get("data")
        latest_line = self._latest_direct_line(symbol=symbol, data=data)
        return self._parse_direct_line(symbol=symbol, data=data, latest_line=latest_line, source=source)

    def _fetch_direct_for_trade_date(self, symbol: str, trade_date: date) -> dict[str, object]:
        market = _infer_market(symbol)
        payload, source = self._request_json(symbol=symbol, market=market)
        data = payload.get("data")
        for line in self._direct_lines(symbol=symbol, data=data):
            parts = line.split(",")
            if parts and _to_trade_date(parts[0]) == trade_date:
                return self._parse_direct_line(symbol=symbol, data=data, latest_line=line, source=source)
        raise CapitalFlowClientError(f"capital flow row missing requested trade date for {symbol}: {trade_date.isoformat()}")

    def _direct_lines(self, *, symbol: str, data: object) -> list[str]:
        if not isinstance(data, dict):
            raise CapitalFlowClientError(f"capital flow payload missing data for {symbol}")
        klines = data.get("klines")
        if not isinstance(klines, list) or not klines:
            raise CapitalFlowClientError(f"capital flow payload missing klines for {symbol}")
        return [line for line in klines if isinstance(line, str)]

    def _latest_direct_line(self, *, symbol: str, data: object) -> str:
        lines = self._direct_lines(symbol=symbol, data=data)
        if not lines:
            raise CapitalFlowClientError(f"capital flow latest row malformed for {symbol}")
        return lines[-1]

    def _parse_direct_line(self, *, symbol: str, data: object, latest_line: str, source: str) -> dict[str, object]:
        if not isinstance(data, dict):
            raise CapitalFlowClientError(f"capital flow payload missing data for {symbol}")
        parts = latest_line.split(",")
        if len(parts) < 13:
            raise CapitalFlowClientError(f"capital flow latest row too short for {symbol}: {len(parts)}")

        trade_date = _to_trade_date(parts[0])
        if trade_date is None:
            raise CapitalFlowClientError(f"capital flow latest row missing trade date for {symbol}")

        return {
            "symbol": str(data.get("code") or symbol),
            "company_name": data.get("name"),
            "trade_date": trade_date,
            "main_net_inflow": _to_float(parts[1]),
            "small_net_inflow": _to_float(parts[2]),
            "medium_net_inflow": _to_float(parts[3]),
            "large_net_inflow": _to_float(parts[4]),
            "super_large_net_inflow": _to_float(parts[5]),
            "main_net_ratio": _to_float(parts[6]),
            "small_net_ratio": _to_float(parts[7]),
            "medium_net_ratio": _to_float(parts[8]),
            "large_net_ratio": _to_float(parts[9]),
            "super_large_net_ratio": _to_float(parts[10]),
            "close_price": _to_float(parts[11]),
            "change_pct": _to_float(parts[12]),
            "source": source,
            "source_status": "fresh",
            "generated_at": None,
            "raw_payload": {
                "code": data.get("code"),
                "name": data.get("name"),
                "market": data.get("market"),
                "line": latest_line,
            },
        }

    def _fetch_latest_akshare(self, symbol: str) -> dict[str, object]:
        market = _infer_market(symbol)
        dataframe = ak.stock_individual_fund_flow(stock=symbol, market=market)
        if dataframe is None or dataframe.empty:
            raise CapitalFlowClientError(f"akshare returned no capital flow rows for {symbol}")

        latest_row = self._latest_dataframe_row(dataframe)
        return self._parse_akshare_row(symbol=symbol, latest_row=latest_row)

    def _fetch_akshare_for_trade_date(self, symbol: str, trade_date: date) -> dict[str, object]:
        market = _infer_market(symbol)
        dataframe = ak.stock_individual_fund_flow(stock=symbol, market=market)
        if dataframe is None or dataframe.empty:
            raise CapitalFlowClientError(f"akshare returned no capital flow rows for {symbol}")
        for row in dataframe.to_dict(orient="records"):
            if _to_trade_date(row.get("日期")) == trade_date:
                return self._parse_akshare_row(symbol=symbol, latest_row=row)
        raise CapitalFlowClientError(f"akshare capital flow row missing requested trade date for {symbol}: {trade_date.isoformat()}")

    def _parse_akshare_row(self, *, symbol: str, latest_row: dict[str, object]) -> dict[str, object]:
        trade_date = _to_trade_date(latest_row.get("日期"))
        if trade_date is None:
            raise CapitalFlowClientError(f"akshare capital flow missing trade date for {symbol}")

        return {
            "symbol": symbol,
            "company_name": None,
            "trade_date": trade_date,
            "main_net_inflow": _to_float(latest_row.get("主力净流入-净额")),
            "small_net_inflow": _to_float(latest_row.get("小单净流入-净额")),
            "medium_net_inflow": _to_float(latest_row.get("中单净流入-净额")),
            "large_net_inflow": _to_float(latest_row.get("大单净流入-净额")),
            "super_large_net_inflow": _to_float(latest_row.get("超大单净流入-净额")),
            "main_net_ratio": _to_float(latest_row.get("主力净流入-净占比")),
            "small_net_ratio": _to_float(latest_row.get("小单净流入-净占比")),
            "medium_net_ratio": _to_float(latest_row.get("中单净流入-净占比")),
            "large_net_ratio": _to_float(latest_row.get("大单净流入-净占比")),
            "super_large_net_ratio": _to_float(latest_row.get("超大单净流入-净占比")),
            "close_price": _to_float(latest_row.get("收盘价")),
            "change_pct": _to_float(latest_row.get("涨跌幅")),
            "source": "akshare-eastmoney",
            "source_status": "fresh",
            "generated_at": None,
            "raw_payload": latest_row,
        }

    def _request_json(self, *, symbol: str, market: str) -> tuple[dict[str, object], str]:
        params = {
            "lmt": "0",
            "klt": "101",
            "secid": f"{MARKET_PREFIX_MAP[market]}.{symbol}",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "_": int(time.time() * 1000),
        }
        last_error: Exception | None = None
        sources = [(self._eastmoney_base_url, "eastmoney-direct")]
        if self._eastmoney_delay_base_url and self._eastmoney_delay_base_url != self._eastmoney_base_url:
            sources.append((self._eastmoney_delay_base_url, "eastmoney-delay"))

        for base_url, source in sources:
            query = urlencode(params)
            request = Request(f"{base_url}?{query}", headers=DEFAULT_HEADERS)
            for attempt in range(1, self._retry_attempts + 1):
                try:
                    with urlopen(request, timeout=self._timeout_seconds) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise CapitalFlowClientError(f"unexpected capital flow payload type for {symbol}")
                    return payload, source
                except CapitalFlowClientError:
                    raise
                except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                    last_error = exc
                    if attempt >= self._retry_attempts:
                        break
                    delay_seconds = self._retry_backoff_seconds * (2 ** (attempt - 1))
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)

        raise CapitalFlowClientError(
            f"capital flow request failed after {self._retry_attempts} attempts for {symbol}: {last_error}"
        ) from last_error

    @staticmethod
    def _latest_dataframe_row(dataframe: pd.DataFrame) -> dict[str, object]:
        rows = dataframe.to_dict(orient="records")
        if not rows:
            raise CapitalFlowClientError("capital flow dataframe unexpectedly empty")
        rows.sort(key=lambda item: _to_trade_date(item.get("日期")) or date.min)
        return rows[-1]
