from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta

import akshare as ak
import pandas as pd


logger = logging.getLogger(__name__)


def _safe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return None
    return text or None


def _to_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _to_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _safe_text(value)
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(text).date()
    except Exception:
        return None


def _normalize_stock_symbol(value: object) -> str | None:
    text = _safe_text(value)
    if not text:
        return None
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) < 6:
        return None
    return digits[:6]


def _quarter_dates(anchor: date | None = None, count: int = 8) -> list[date]:
    current = anchor or datetime.now(UTC).date()
    year = current.year
    quarters = [(3, 31), (6, 30), (9, 30), (12, 31)]
    candidates: list[date] = []
    while len(candidates) < count:
        for month, day in reversed(quarters):
            candidate = date(year, month, day)
            if candidate <= current:
                candidates.append(candidate)
                if len(candidates) >= count:
                    break
        year -= 1
    return candidates


class FundDataClient:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._last_request_at = 0.0
        self._name_cache: dict[str, dict[str, object]] = {}

    def fetch_fund_state(self, fund_code: str) -> dict[str, object]:
        profile = self.fetch_profile(fund_code)
        nav_history = self.fetch_nav_history(fund_code)
        holdings = self.fetch_top_holdings(fund_code)
        latest_nav = nav_history[0] if nav_history else {}
        estimated_return = self._estimate_intraday_return(holdings)
        snapshot = {
            "fundCode": fund_code,
            "fundName": profile.get("fundName") or latest_nav.get("fundName") or fund_code,
            "fundType": profile.get("fundType"),
            "nav": latest_nav.get("nav"),
            "accumNav": latest_nav.get("accum_nav"),
            "dailyReturn": latest_nav.get("daily_return"),
            "navDate": latest_nav.get("nav_date"),
            "estimatedIntradayReturn": estimated_return,
            "topHoldingsPreview": [
                {
                    "stockSymbol": item["stock_symbol"],
                    "stockName": item.get("stock_name"),
                    "weightPercent": item.get("weight_percent"),
                }
                for item in holdings[:5]
            ],
            "source": "akshare",
        }
        return {
            "profile": profile,
            "snapshot": snapshot,
            "nav_history": nav_history,
            "holdings": holdings,
        }

    def fetch_profile(self, fund_code: str) -> dict[str, object]:
        if not self._name_cache:
            self._wait_for_slot()
            frame = ak.fund_name_em()
            for row in frame.to_dict("records"):
                code = _safe_text(row.get("基金代码") or row.get("fund_code") or row.get("代码"))
                if code:
                    self._name_cache[code] = row
        row = self._name_cache.get(fund_code) or {}
        fund_name = _safe_text(row.get("基金简称") or row.get("基金名称") or row.get("fund_name")) or fund_code
        fund_type = _safe_text(row.get("基金类型") or row.get("类型") or row.get("fund_type"))
        profile = {
            "fundCode": fund_code,
            "fundName": fund_name,
            "fundType": fund_type,
            "source": "akshare-fund_name_em",
            "rawPayload": row,
        }
        index_profile = self._fetch_index_profile(fund_code)
        if index_profile:
            profile.update(index_profile)
        return profile

    def fetch_nav_history(self, fund_code: str, limit: int = 60) -> list[dict[str, object]]:
        self._wait_for_slot()
        source = "akshare-fund_open_fund_info_em"
        try:
            frame = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势", period="成立来")
        except Exception:
            source = "akshare-fund_etf_fund_info_em"
            frame = ak.fund_etf_fund_info_em(fund=fund_code, start_date="20000101", end_date="20500101")
        rows: list[dict[str, object]] = []
        for row in frame.to_dict("records"):
            nav_date = _to_date(row.get("净值日期") or row.get("日期"))
            if nav_date is None:
                continue
            rows.append(
                {
                    "fund_code": fund_code,
                    "nav_date": nav_date,
                    "nav": _to_float(row.get("单位净值")),
                    "accum_nav": _to_float(row.get("累计净值")),
                    "daily_return": _to_float(row.get("日增长率") or row.get("日增长值") or row.get("日增长率(%)")),
                    "source": source,
                    "raw": row,
                }
            )
        return sorted(rows, key=lambda item: item["nav_date"], reverse=True)[:limit]

    def fetch_top_holdings(self, fund_code: str) -> list[dict[str, object]]:
        for report_date in _quarter_dates(count=8):
            self._wait_for_slot()
            try:
                frame = ak.stock_report_fund_hold_detail(symbol=fund_code, date=report_date.strftime("%Y%m%d"))
            except Exception:
                logger.info("fund holdings fetch failed for report date", extra={"fund_code": fund_code, "report_date": report_date.isoformat()})
                continue
            rows = self._normalize_fund_holdings(fund_code, report_date, frame)
            if rows:
                return rows[:10]
        return []

    def fetch_stock_fund_holders(self, symbol: str) -> list[dict[str, object]]:
        self._wait_for_slot()
        frame = ak.stock_fund_stock_holder(symbol=symbol)
        rows: list[dict[str, object]] = []
        for row in frame.to_dict("records"):
            fund_code = _safe_text(row.get("基金代码"))
            report_date = _to_date(row.get("截止日期") or row.get("报告期"))
            if not fund_code or report_date is None:
                continue
            rows.append(
                {
                    "stock_symbol": symbol,
                    "fund_code": fund_code,
                    "fund_name": _safe_text(row.get("基金名称")),
                    "fund_type": None,
                    "report_date": report_date,
                    "weight_percent": _to_float(row.get("占净值比例")),
                    "hold_market_value": _to_float(row.get("持股市值")),
                    "change_type": None,
                    "raw": row,
                }
            )
        return sorted(rows, key=lambda item: item["report_date"], reverse=True)

    def _fetch_index_profile(self, fund_code: str) -> dict[str, object]:
        try:
            self._wait_for_slot()
            frame = ak.fund_info_index_em(symbol="全部", indicator="全部")
        except Exception:
            return {}
        match = frame[frame["基金代码"].astype(str) == fund_code] if "基金代码" in frame.columns else pd.DataFrame()
        if match.empty:
            return {}
        row = match.iloc[0].to_dict()
        return {
            "fundName": _safe_text(row.get("基金名称")) or fund_code,
            "benchmarkIndex": _safe_text(row.get("跟踪标的")),
            "managementFee": _to_float(row.get("手续费")),
            "source": "akshare-fund_info_index_em",
            "indexPayload": row,
        }

    def _normalize_fund_holdings(self, fund_code: str, report_date: date, frame) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        if frame is None or getattr(frame, "empty", True):
            return rows
        for row in frame.to_dict("records"):
            symbol = _normalize_stock_symbol(row.get("股票代码"))
            if symbol is None:
                continue
            rows.append(
                {
                    "fund_code": fund_code,
                    "stock_symbol": symbol,
                    "stock_name": _safe_text(row.get("股票简称") or row.get("股票名称")),
                    "report_date": report_date,
                    "rank": _to_int(row.get("序号")),
                    "weight_percent": _to_float(row.get("占净值比例") or row.get("占基金净值比例")),
                    "hold_shares": _to_int(row.get("持股数") or row.get("持仓数量")),
                    "hold_market_value": _to_float(row.get("持股市值")),
                    "change_type": _safe_text(row.get("变动情况") or row.get("持仓变动")),
                    "raw": row,
                }
            )
        return sorted(rows, key=lambda item: (item.get("rank") or 9999, item["stock_symbol"]))

    def _estimate_intraday_return(self, holdings: list[dict[str, object]]) -> float | None:
        weighted_values = [item.get("weight_percent") for item in holdings if isinstance(item.get("weight_percent"), (int, float))]
        if not weighted_values:
            return None
        return 0.0

    def _wait_for_slot(self) -> None:
        interval = max(float(getattr(self._settings, "fund_collector_request_interval_seconds", 1.0)), 0.0)
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_at = time.monotonic()
