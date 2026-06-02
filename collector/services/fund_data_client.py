from __future__ import annotations

import logging
import re
import time
from datetime import UTC, date, datetime, timedelta

import akshare as ak
import pandas as pd
import requests


logger = logging.getLogger(__name__)


_DANJUAN_FUND_URL = "https://danjuanfunds.com/djapi/fund/{fund_code}"
_DANJUAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
_FUND_RISK_LEVEL_LABELS = {
    "1": "低风险",
    "2": "中低风险",
    "3": "中风险",
    "4": "中高风险",
    "5": "高风险",
}


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


def _merge_present_values(target: dict[str, object], updates: dict[str, object]) -> None:
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if key == "source" and target.get("source"):
            continue
        if key == "profileSources":
            existing_sources = target.setdefault("profileSources", [])
            if not isinstance(existing_sources, list) or not isinstance(value, list):
                continue
            for source in value:
                if source not in existing_sources:
                    existing_sources.append(source)
            continue
        target[key] = value


def _normalize_stock_symbol(value: object) -> str | None:
    text = _safe_text(value)
    if not text:
        return None
    normalized = text.replace(" ", "").upper()
    if not normalized:
        return None
    if normalized.endswith((".SH", ".SZ")):
        base = normalized.rsplit(".", 1)[0]
        if base.isdigit() and len(base) >= 6:
            return base[:6]
    if normalized.startswith(("SH", "SZ")) and normalized[2:].isdigit() and len(normalized) >= 8:
        return normalized[2:8]
    digits = "".join(character for character in normalized if character.isdigit())
    if len(digits) >= 6 and normalized == digits:
        return digits[:6]
    if len(normalized) <= 16:
        return normalized
    return normalized[:16]


def _infer_stock_market(symbol: str) -> str | None:
    if not symbol:
        return None
    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith(("5", "6", "9")):
            return "SH"
        return "SZ"
    if "." in symbol:
        suffix = symbol.rsplit(".", 1)[-1]
        if suffix in {"HK", "US", "SH", "SZ"}:
            return suffix
    if symbol.startswith(("HK", "US")):
        return symbol[:2]
    if symbol.isdigit() and len(symbol) < 6:
        return "HK"
    if symbol.isalpha():
        return "US"
    return None


def _is_stock_collector_supported_symbol(symbol: str | None) -> bool:
    if not symbol:
        return False
    return symbol.isdigit() and len(symbol) == 6


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


def _quarter_label_to_date(value: object) -> date | None:
    text = _safe_text(value)
    if not text:
        return None
    normalized = text.replace(" ", "")
    match = re.search(r"(\d{4})[年/-]?第?([1-4])[季度Qq]+", normalized)
    if not match:
        match = re.search(r"(\d{4})[Qq]([1-4])", normalized)
    if not match:
        return None
    year = int(match.group(1))
    quarter = int(match.group(2))
    month_day_map = {
        1: (3, 31),
        2: (6, 30),
        3: (9, 30),
        4: (12, 31),
    }
    month, day = month_day_map[quarter]
    return date(year, month, day)


def _to_ten_thousand_float(value: object) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    return number * 10_000


def _to_ten_thousand_int(value: object) -> int | None:
    number = _to_ten_thousand_float(value)
    if number is None:
        return None
    return int(round(number))


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
            _merge_present_values(profile, index_profile)
        xq_basic_profile = self._fetch_xq_basic_profile(fund_code)
        if xq_basic_profile:
            _merge_present_values(profile, xq_basic_profile)
        xq_raw_profile = self._fetch_xq_raw_profile(fund_code)
        if xq_raw_profile:
            _merge_present_values(profile, xq_raw_profile)
        xq_fee_profile = self._fetch_xq_fee_profile(fund_code)
        if xq_fee_profile:
            _merge_present_values(profile, xq_fee_profile)
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
        primary_rows = self._fetch_top_holdings_from_portfolio(fund_code)
        if primary_rows:
            return primary_rows[:10]

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

    def _fetch_top_holdings_from_portfolio(self, fund_code: str) -> list[dict[str, object]]:
        years_to_try: list[str] = []
        current_year = datetime.now(UTC).year
        for year in (current_year, current_year - 1):
            year_text = str(year)
            if year_text not in years_to_try:
                years_to_try.append(year_text)

        for year in years_to_try:
            self._wait_for_slot()
            try:
                frame = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
            except Exception:
                logger.info(
                    "fund portfolio holdings fetch failed",
                    extra={"fund_code": fund_code, "year": year},
                )
                continue
            rows = self._normalize_portfolio_holdings(fund_code, frame)
            if rows:
                return rows[:10]
        return []

    def fetch_stock_fund_holders(self, symbol: str) -> list[dict[str, object]]:
        self._wait_for_slot()
        try:
            frame = ak.stock_fund_stock_holder(symbol=symbol)
        except Exception as exc:
            logger.info("stock fund holders unavailable", extra={"symbol": symbol, "error": str(exc)})
            return []
        rows: list[dict[str, object]] = []
        for row in frame.to_dict("records"):
            fund_code = _safe_text(row.get("基金代码"))
            report_date = _to_date(row.get("截止日期") or row.get("报告期"))
            if not fund_code or report_date is None:
                continue
            rows.append(
                {
                    "stock_symbol": symbol,
                    "stock_market": _infer_stock_market(symbol),
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
            "purchaseFee": _to_float(row.get("手续费")),
            "source": "akshare-fund_info_index_em",
            "indexPayload": row,
        }

    def _fetch_xq_basic_profile(self, fund_code: str) -> dict[str, object]:
        try:
            self._wait_for_slot()
            frame = ak.fund_individual_basic_info_xq(symbol=fund_code, timeout=15.0)
        except Exception as exc:
            logger.info("fund xq basic profile unavailable", extra={"fund_code": fund_code, "error": str(exc)})
            return {}
        rows = frame.to_dict("records")
        values = {_safe_text(row.get("item")): row.get("value") for row in rows if _safe_text(row.get("item"))}
        return {
            "fundName": _safe_text(values.get("基金名称")),
            "fundFullName": _safe_text(values.get("基金全称")),
            "fundType": _safe_text(values.get("基金类型")),
            "fundCompany": _safe_text(values.get("基金公司")),
            "managerName": _safe_text(values.get("基金经理")),
            "establishedDate": _to_date(values.get("成立时间")),
            "custodianBank": _safe_text(values.get("托管银行")),
            "ratingAgency": _safe_text(values.get("评级机构")),
            "fundRating": _safe_text(values.get("基金评级")),
            "investmentStrategy": _safe_text(values.get("投资策略")),
            "investmentObjective": _safe_text(values.get("投资目标")),
            "performanceBenchmark": _safe_text(values.get("业绩比较基准")),
            "profileSources": ["akshare-fund_individual_basic_info_xq"],
        }

    def _fetch_xq_raw_profile(self, fund_code: str) -> dict[str, object]:
        try:
            self._wait_for_slot()
            response = requests.get(_DANJUAN_FUND_URL.format(fund_code=fund_code), headers=_DANJUAN_HEADERS, timeout=15.0)
            response.raise_for_status()
            payload = response.json().get("data")
        except Exception as exc:
            logger.info("fund xq raw profile unavailable", extra={"fund_code": fund_code, "error": str(exc)})
            return {}
        if not isinstance(payload, dict):
            return {}

        risk_level_code = _safe_text(payload.get("risk_level"))
        risk_level = self._extract_risk_level_label(payload) or (risk_level_code and _FUND_RISK_LEVEL_LABELS.get(risk_level_code))
        return {
            "riskLevel": risk_level,
            "riskLevelCode": risk_level_code,
            "profileSources": ["danjuan-fund-api"],
        }

    def _fetch_xq_fee_profile(self, fund_code: str) -> dict[str, object]:
        try:
            self._wait_for_slot()
            frame = ak.fund_individual_detail_info_xq(symbol=fund_code, timeout=15.0)
        except Exception as exc:
            logger.info("fund xq fee profile unavailable", extra={"fund_code": fund_code, "error": str(exc)})
            return {}
        rows = frame.to_dict("records")
        fee_profile: dict[str, object] = {"profileSources": ["akshare-fund_individual_detail_info_xq"]}
        for row in rows:
            if _safe_text(row.get("费用类型")) != "其他费用":
                continue
            name = _safe_text(row.get("条件或名称"))
            if name == "基金管理费":
                fee_profile["managementFee"] = _to_float(row.get("费用"))
            elif name == "基金托管费":
                fee_profile["custodyFee"] = _to_float(row.get("费用"))
        return fee_profile

    def _extract_risk_level_label(self, payload: dict[str, object]) -> str | None:
        op_fund = payload.get("op_fund")
        if not isinstance(op_fund, dict):
            return None
        fund_tags = op_fund.get("fund_tags")
        if not isinstance(fund_tags, list):
            return None
        for tag in fund_tags:
            if not isinstance(tag, dict):
                continue
            name = _safe_text(tag.get("name"))
            category = _safe_text(tag.get("category"))
            if name and (category == "9" or "风险" in name):
                return name
        return None

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
                    "stock_market": _infer_stock_market(symbol),
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

    def _normalize_portfolio_holdings(self, fund_code: str, frame) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        if frame is None or getattr(frame, "empty", True):
            return rows

        for row in frame.to_dict("records"):
            symbol = _normalize_stock_symbol(row.get("股票代码"))
            report_date = _quarter_label_to_date(row.get("季度"))
            if symbol is None or report_date is None:
                continue
            rows.append(
                {
                    "fund_code": fund_code,
                    "stock_symbol": symbol,
                    "stock_market": _infer_stock_market(symbol),
                    "stock_name": _safe_text(row.get("股票名称") or row.get("股票简称")),
                    "report_date": report_date,
                    "rank": _to_int(row.get("序号")),
                    "weight_percent": _to_float(row.get("占净值比例") or row.get("占基金净值比例")),
                    "hold_shares": _to_ten_thousand_int(row.get("持股数") or row.get("持仓数量")),
                    "hold_market_value": _to_ten_thousand_float(row.get("持仓市值") or row.get("持股市值")),
                    "change_type": _safe_text(row.get("变动情况") or row.get("持仓变动")),
                    "raw": {**row, "source": "akshare-fund_portfolio_hold_em"},
                }
            )

        rows.sort(key=lambda item: (item["report_date"], -(item.get("rank") or 9999)), reverse=True)
        latest_report_date = rows[0]["report_date"] if rows else None
        if latest_report_date is None:
            return []
        latest_rows = [item for item in rows if item["report_date"] == latest_report_date]
        return sorted(latest_rows, key=lambda item: (item.get("rank") or 9999, item["stock_symbol"]))

    def _estimate_intraday_return(self, holdings: list[dict[str, object]]) -> float | None:
        return None

    def _wait_for_slot(self) -> None:
        interval = max(float(getattr(self._settings, "fund_collector_request_interval_seconds", 1.0)), 0.0)
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_at = time.monotonic()
