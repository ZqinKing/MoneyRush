from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


EASTMONEY_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/stock/lhb.html",
    "Accept": "application/json,text/plain,*/*",
}


class DragonTigerClientError(RuntimeError):
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


def _to_int(value: object) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


def _to_iso_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.date().isoformat()
    return text[:10] if len(text) >= 10 else text


class DragonTigerClient:
    def __init__(self, *, timeout_seconds: float = 15.0, retry_attempts: int = 3, retry_backoff_seconds: float = 0.6) -> None:
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = max(int(retry_attempts), 1)
        self._retry_backoff_seconds = max(float(retry_backoff_seconds), 0.0)

    def _request_json(self, params: dict[str, object]) -> dict[str, object]:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        request = Request(f"{EASTMONEY_DATACENTER_URL}?{query}", headers=DEFAULT_HEADERS)
        last_error: Exception | None = None

        for attempt in range(1, self._retry_attempts + 1):
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise DragonTigerClientError("unexpected Eastmoney response payload")
                return payload
            except DragonTigerClientError:
                raise
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self._retry_attempts:
                    break
                delay_seconds = self._retry_backoff_seconds * attempt
                if delay_seconds > 0:
                    time.sleep(delay_seconds)

        raise DragonTigerClientError(
            f"eastmoney request failed after {self._retry_attempts} attempts: {last_error}"
        ) from last_error

    def _fetch_paginated(self, *, report_name: str, columns: str, sort_columns: str, sort_types: str, filter_expression: str) -> list[dict[str, object]]:
        page = 1
        pages = 1
        rows: list[dict[str, object]] = []
        while page <= pages:
            payload = self._request_json(
                {
                    "reportName": report_name,
                    "columns": columns,
                    "sortColumns": sort_columns,
                    "sortTypes": sort_types,
                    "pageSize": 500,
                    "pageNumber": page,
                    "filter": filter_expression,
                    "source": "WEB",
                    "client": "WEB",
                }
            )
            result = payload.get("result")
            if not isinstance(result, dict):
                break
            data = result.get("data")
            if not isinstance(data, list):
                break
            rows.extend(item for item in data if isinstance(item, dict))
            pages = int(result.get("pages") or 1)
            page += 1
        return rows

    def fetch_daily(self, *, trade_date: str) -> dict[str, object]:
        rows = self._fetch_paginated(
            report_name="RPT_DAILYBILLBOARD_DETAILSNEW",
            columns=(
                "SECURITY_CODE,SECUCODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLAIN,CLOSE_PRICE,CHANGE_RATE,"
                "BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_DEAL_AMT,ACCUM_AMOUNT,"
                "DEAL_NET_RATIO,DEAL_AMOUNT_RATIO,TURNOVERRATE,FREE_MARKET_CAP,EXPLANATION,"
                "D1_CLOSE_ADJCHRATE,D2_CLOSE_ADJCHRATE,D5_CLOSE_ADJCHRATE,D10_CLOSE_ADJCHRATE"
            ),
            sort_columns="SECURITY_CODE,TRADE_DATE",
            sort_types="1,-1",
            filter_expression=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        )
        items = [self._normalize_daily_row(row) for row in rows]
        return {
            "items": items,
            "tradeDate": trade_date,
            "source": "eastmoney-datacenter",
            "generatedAt": datetime.now(UTC).isoformat(),
        }

    def fetch_institution_trade_details(self, *, start_date: str, end_date: str) -> dict[str, object]:
        rows = self._fetch_paginated(
            report_name="RPT_ORGANIZATION_TRADE_DETAILS",
            columns="ALL",
            sort_columns="NET_BUY_AMT,TRADE_DATE,SECURITY_CODE",
            sort_types="-1,-1,1",
            filter_expression=f"(TRADE_DATE>='{start_date}')(TRADE_DATE<='{end_date}')",
        )
        items = [self._normalize_institution_trade_row(row) for row in rows]
        return {
            "items": items,
            "startDate": start_date,
            "endDate": end_date,
            "source": "eastmoney-datacenter",
            "generatedAt": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _normalize_daily_row(row: dict[str, object]) -> dict[str, object]:
        return {
            "symbol": row.get("SECURITY_CODE"),
            "secuCode": row.get("SECUCODE"),
            "name": row.get("SECURITY_NAME_ABBR"),
            "tradeDate": _to_iso_date(row.get("TRADE_DATE")),
            "closePrice": _to_float(row.get("CLOSE_PRICE")),
            "changePercent": _to_float(row.get("CHANGE_RATE")),
            "netBuyAmount": _to_float(row.get("BILLBOARD_NET_AMT")),
            "buyAmount": _to_float(row.get("BILLBOARD_BUY_AMT")),
            "sellAmount": _to_float(row.get("BILLBOARD_SELL_AMT")),
            "dealAmount": _to_float(row.get("BILLBOARD_DEAL_AMT")),
            "totalAmount": _to_float(row.get("ACCUM_AMOUNT")),
            "netBuyRatio": _to_float(row.get("DEAL_NET_RATIO")),
            "dealAmountRatio": _to_float(row.get("DEAL_AMOUNT_RATIO")),
            "turnoverRate": _to_float(row.get("TURNOVERRATE")),
            "freeMarketCap": _to_float(row.get("FREE_MARKET_CAP")),
            "explain": row.get("EXPLAIN"),
            "reason": row.get("EXPLANATION"),
            "after1d": _to_float(row.get("D1_CLOSE_ADJCHRATE")),
            "after2d": _to_float(row.get("D2_CLOSE_ADJCHRATE")),
            "after5d": _to_float(row.get("D5_CLOSE_ADJCHRATE")),
            "after10d": _to_float(row.get("D10_CLOSE_ADJCHRATE")),
        }

    @staticmethod
    def _normalize_institution_trade_row(row: dict[str, object]) -> dict[str, object]:
        return {
            "symbol": row.get("SECURITY_CODE"),
            "secuCode": row.get("SECUCODE"),
            "name": row.get("SECURITY_NAME_ABBR"),
            "tradeDate": _to_iso_date(row.get("TRADE_DATE")),
            "closePrice": _to_float(row.get("CLOSE_PRICE")),
            "changePercent": _to_float(row.get("CHANGE_RATE")),
            "buyOrgCount": _to_int(row.get("BUY_TIMES")),
            "sellOrgCount": _to_int(row.get("SELL_TIMES")),
            "orgBuyAmount": _to_float(row.get("BUY_AMT")),
            "orgSellAmount": _to_float(row.get("SELL_AMT")),
            "orgNetAmount": _to_float(row.get("NET_BUY_AMT")),
            "marketTotalAmount": _to_float(row.get("ACCUM_AMOUNT")),
            "orgNetAmountRatio": _to_float(row.get("RATIO")),
            "turnoverRate": _to_float(row.get("TURNOVERRATE")),
            "freeMarketCap": _to_float(row.get("FREECAP")),
            "reason": row.get("EXPLANATION"),
        }
