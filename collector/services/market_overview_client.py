from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import logging
import re
from time import monotonic
from urllib.request import Request, urlopen

import akshare as ak
from bs4 import BeautifulSoup
import requests

from collector.services.tencent_quote_client import _parse_timestamp


INDEX_DEFINITIONS = (
    ("000001", "000001.SH", "上证指数"),
    ("399001", "399001.SZ", "深证成指"),
    ("399006", "399006.SZ", "创业板指"),
    ("000688", "000688.SH", "科创50"),
)

TENCENT_INDEX_VENDOR_CODES = {
    "000001": "sh000001",
    "399001": "sz399001",
    "399006": "sz399006",
    "000688": "sh000688",
}

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://stockapp.finance.qq.com/",
}

logger = logging.getLogger(__name__)
CHINA_MARKET_TZ = timezone(timedelta(hours=8))

LEGU_BREADTH_URL = "https://www.legulegu.com/stockdata/market-activity"
LEGU_BREADTH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": LEGU_BREADTH_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

LEGU_DESCRIPTION_RE = re.compile(
    r"其中(?P<limit_up>\d+)家涨停，(?P<limit_down>\d+)家跌停，(?P<up>\d+)家上涨，(?P<down>\d+)家下跌",
)
LEGU_OVERVIEW_RE = re.compile(
    r"<td>\s*上涨\s*</td>\s*<td[^>]*>\s*(?P<up>\d+)\s*</td>\s*"
    r"<td>\s*下跌\s*</td>\s*<td[^>]*>\s*(?P<down>\d+)\s*</td>\s*"
    r"<td>\s*平盘\s*</td>\s*<td[^>]*>\s*(?P<flat>\d+)\s*</td>",
    re.S,
)
LEGU_LIMIT_RE = re.compile(
    r"<td>\s*涨停\s*</td>\s*<td[^>]*>\s*(?P<limit_up>\d+)\s*</td>\s*"
    r"<td>\s*跌停\s*</td>\s*<td[^>]*>\s*(?P<limit_down>\d+)\s*</td>\s*"
    r"<td>\s*停牌\s*</td>\s*<td[^>]*>\s*(?P<suspend>\d+)\s*</td>",
    re.S,
)
LEGU_UPDATED_AT_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        normalized = str(value).replace(",", "").strip()
        if not normalized or normalized == "--":
            return None
        return float(normalized)
    except (TypeError, ValueError):
        return None


def build_market_status(*, now: datetime | None = None) -> tuple[str, bool]:
    from datetime import timedelta, timezone

    current = now.astimezone(timezone(timedelta(hours=8))) if now is not None else datetime.now(timezone(timedelta(hours=8)))
    if current.weekday() >= 5:
        return "closed", False

    current_minutes = current.hour * 60 + current.minute
    if 570 <= current_minutes < 690 or 780 <= current_minutes < 900:
        return "trading", True
    if 690 <= current_minutes < 780:
        return "break", False
    return "closed", False


class MarketOverviewClient:
    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._tencent_index_cache: dict[str, dict[str, object]] = {}
        self._tencent_index_cache_at = 0.0
        self._tencent_failure_until = 0.0
        self._breadth_cache: dict[str, object] | None = None
        self._breadth_cache_at = 0.0
        self._breadth_failure_until = 0.0

    def fetch(self) -> dict[str, object]:
        generated_at = datetime.now(UTC)
        market_status, is_trading_session = build_market_status(now=generated_at)
        fallback_rows = self._get_tencent_index_rows([short_code for short_code, _full_code, _name in INDEX_DEFINITIONS])

        indexes = []
        for short_code, full_code, name in INDEX_DEFINITIONS:
            row = fallback_rows.get(short_code, {})
            row_updated_at = row.get("updatedAt")
            indexes.append(
                {
                    "symbol": short_code,
                    "code": full_code,
                    "name": name,
                    "lastPrice": row.get("lastPrice"),
                    "changePct": row.get("changePct"),
                    "changeAmount": row.get("changeAmount"),
                    "updatedAt": row_updated_at if isinstance(row_updated_at, str) else generated_at.isoformat(),
                    "source": row.get("source") or "tencent-finance",
                }
            )

        breadth = self._get_direct_breadth()

        return {
            "generatedAt": generated_at.isoformat(),
            "marketStatus": market_status,
            "isTradingSession": is_trading_session,
            "serverGeneratedAt": generated_at.isoformat(),
            "indexes": indexes,
            "breadth": breadth,
        }

    def _get_direct_breadth(self) -> dict[str, object] | None:
        if not self._is_legu_breadth_enabled():
            return self._breadth_cache

        now = monotonic()
        refresh_seconds = self._get_legu_breadth_refresh_seconds()
        if self._breadth_cache and now - self._breadth_cache_at < refresh_seconds:
            return self._breadth_cache

        if now < self._breadth_failure_until:
            return self._breadth_cache

        try:
            payload = self._fetch_legu_breadth_payload()
        except Exception:
            self._breadth_failure_until = now + self._get_legu_breadth_failure_cooldown_seconds()
            logger.exception("market overview breadth fetch failed")
            return self._breadth_cache

        breadth = self._normalize_breadth_payload(payload)
        if breadth is None:
            return self._breadth_cache

        self._breadth_cache = breadth
        self._breadth_cache_at = monotonic()
        self._breadth_failure_until = 0.0
        return breadth

    def _fetch_legu_breadth_payload(self) -> dict[str, object]:
        try:
            frame = ak.stock_market_activity_legu()
        except Exception:
            logger.warning("akshare stock_market_activity_legu failed; falling back to direct Legu parser", exc_info=True)
        else:
            payload = self._normalize_akshare_legu_frame(frame)
            if payload is not None:
                return payload

        request = requests.get(LEGU_BREADTH_URL, headers=LEGU_BREADTH_HEADERS, timeout=self._get_legu_breadth_timeout_seconds())
        request.raise_for_status()
        html = request.text
        soup = BeautifulSoup(html, features="lxml")

        payload: dict[str, object] = {
            "source": "legulegu",
            "provider": "akshare",
        }

        description = soup.find("meta", attrs={"name": "description"})
        description_text = str(description.get("content") or "") if description is not None else ""
        if description_text:
            payload["description"] = description_text
            description_match = LEGU_DESCRIPTION_RE.search(description_text)
            if description_match is not None:
                payload["limit_up_count"] = int(description_match.group("limit_up"))
                payload["limit_down_count"] = int(description_match.group("limit_down"))
                payload["up_count"] = int(description_match.group("up"))
                payload["down_count"] = int(description_match.group("down"))

        overview_match = LEGU_OVERVIEW_RE.search(html)
        if overview_match is not None:
            payload["up_count"] = int(overview_match.group("up"))
            payload["down_count"] = int(overview_match.group("down"))
            payload["flat_count"] = int(overview_match.group("flat"))

        limit_match = LEGU_LIMIT_RE.search(html)
        if limit_match is not None:
            payload["limit_up_count"] = int(limit_match.group("limit_up"))
            payload["limit_down_count"] = int(limit_match.group("limit_down"))
            payload["suspend_count"] = int(limit_match.group("suspend"))

        updated_at = datetime.now(UTC)
        updated_match = LEGU_UPDATED_AT_RE.search(html)
        if updated_match is not None:
            try:
                updated_at = datetime.strptime(updated_match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA_MARKET_TZ).astimezone(UTC)
            except ValueError:
                updated_at = datetime.now(UTC)
        payload["updatedAt"] = updated_at.isoformat()

        sample_size = sum(
            int(value)
            for value in (
                payload.get("up_count"),
                payload.get("down_count"),
                payload.get("flat_count"),
                payload.get("suspend_count"),
            )
            if isinstance(value, int)
        )
        if sample_size:
            payload["sampleSize"] = sample_size
        return payload

    @staticmethod
    def _normalize_akshare_legu_frame(frame) -> dict[str, object] | None:
        if frame is None or getattr(frame, "empty", True):
            return None

        metrics: dict[str, object] = {}
        for row in frame.to_dict(orient="records"):
            item = str(row.get("item") or "").strip()
            value = row.get("value")
            if item:
                metrics[item] = value

        payload: dict[str, object] = {
            "up_count": metrics.get("上涨") or metrics.get("上涨家数"),
            "down_count": metrics.get("下跌") or metrics.get("下跌家数"),
            "flat_count": metrics.get("平盘") or metrics.get("平盘家数"),
            "limit_up_count": metrics.get("涨停板数") or metrics.get("涨停"),
            "limit_down_count": metrics.get("跌停板数") or metrics.get("跌停"),
            "suspend_count": metrics.get("停牌"),
            "updatedAt": metrics.get("统计日期") or datetime.now(UTC).isoformat(),
            "source": "akshare-legulegu",
            "provider": "akshare",
        }
        sample_size = sum(
            int(float(value))
            for value in (
                payload.get("up_count"),
                payload.get("down_count"),
                payload.get("flat_count"),
                payload.get("suspend_count"),
            )
            if _to_float(value) is not None
        )
        if sample_size:
            payload["sampleSize"] = sample_size
        if payload.get("up_count") is None and payload.get("down_count") is None:
            return None
        return payload

    @staticmethod
    def _normalize_breadth_payload(payload: dict[str, object]) -> dict[str, object] | None:
        def pick(*keys: str) -> object | None:
            for key in keys:
                value = payload.get(key)
                if value is not None:
                    return value
            return None

        advance_count = _to_float(pick("advanceCount", "up_count", "up_num", "UpHome", "rise_num"))
        decline_count = _to_float(pick("declineCount", "down_count", "down_num", "DownHome", "fall_num"))
        flat_count = _to_float(pick("flatCount", "flat_count", "flat_num", "equal_count"))
        limit_up_count = _to_float(pick("limitUpCount", "limit_up_count", "up_limit_count", "limitUp", "UpLimit"))
        limit_down_count = _to_float(pick("limitDownCount", "limit_down_count", "down_limit_count", "limitDown", "DownLimit"))
        suspend_count = _to_float(pick("suspendCount", "suspend_count", "halt_count", "paused_count"))

        if advance_count is None and decline_count is None and flat_count is None:
            return None

        generated_at = pick("updatedAt", "updated_at", "generatedAt", "generated_at", "time")
        sample_size = _to_float(pick("sampleSize", "totalCount", "total_count", "count", "itemNum", "ItemNum"))
        if sample_size is None:
            sample_size = sum(
                value
                for value in (advance_count or 0, decline_count or 0, flat_count or 0, suspend_count or 0)
                if isinstance(value, (int, float))
            )
        breadth: dict[str, object] = {
            "advanceCount": int(advance_count or 0),
            "declineCount": int(decline_count or 0),
            "flatCount": int(flat_count or 0),
            "limitUpCount": int(limit_up_count or 0),
            "limitDownCount": int(limit_down_count or 0),
            "sampleSize": int(sample_size or 0),
            "updatedAt": generated_at if isinstance(generated_at, str) else datetime.now(UTC).isoformat(),
            "source": str(pick("source", "provider") or "legulegu"),
            "degraded": False,
        }
        return breadth

    def _get_tencent_index_rows(self, symbols: list[str]) -> dict[str, dict[str, object]]:
        if not self._is_tencent_fallback_enabled():
            return {}

        now = monotonic()
        refresh_seconds = self._get_tencent_refresh_seconds()
        if self._tencent_index_cache and now - self._tencent_index_cache_at < refresh_seconds:
            return {symbol: row for symbol, row in self._tencent_index_cache.items() if symbol in symbols}

        if now < self._tencent_failure_until:
            return {symbol: row for symbol, row in self._tencent_index_cache.items() if symbol in symbols}

        try:
            rows = self._fetch_tencent_index_rows(symbols)
        except Exception:
            self._tencent_failure_until = now + self._get_tencent_failure_cooldown_seconds()
            logger.exception("market overview Tencent fallback fetch failed")
            return {symbol: row for symbol, row in self._tencent_index_cache.items() if symbol in symbols}

        if rows:
            self._tencent_index_cache.update(rows)
            self._tencent_index_cache_at = monotonic()
            self._tencent_failure_until = 0.0
        return {symbol: row for symbol, row in self._tencent_index_cache.items() if symbol in symbols}

    def _fetch_tencent_index_rows(self, symbols: list[str]) -> dict[str, dict[str, object]]:
        vendor_codes = [TENCENT_INDEX_VENDOR_CODES[symbol] for symbol in symbols if symbol in TENCENT_INDEX_VENDOR_CODES]
        if not vendor_codes:
            return {}
        request = Request(f"{TENCENT_QUOTE_URL}{','.join(vendor_codes)}", headers=TENCENT_HEADERS)
        with urlopen(request, timeout=10) as response:
            payload = response.read().decode("gbk", errors="replace").strip()

        if not payload:
            raise ValueError("empty Tencent index fallback payload")

        rows: dict[str, dict[str, object]] = {}
        for raw_item in payload.split(";"):
            line = raw_item.strip()
            if not line or "pv_none_match" in line or '="' not in line:
                continue
            _, quoted_value = line.split('="', 1)
            parts = quoted_value.removesuffix('"').split("~")
            if len(parts) < 33:
                continue

            symbol = str(parts[2] or "").zfill(6)
            last_price = _to_float(parts[3])
            previous_close = _to_float(parts[4])
            change_pct = _to_float(parts[32])
            updated_at_raw = parts[30] if len(parts) > 30 else None
            updated_at = _parse_timestamp(updated_at_raw) if isinstance(updated_at_raw, str) and updated_at_raw else datetime.now(UTC)

            if last_price is None:
                continue

            change_amount = None
            if previous_close is not None:
                change_amount = round(last_price - previous_close, 2)

            rows[symbol] = {
                "lastPrice": last_price,
                "changePct": change_pct,
                "changeAmount": change_amount,
                "updatedAt": updated_at.isoformat(),
                "source": "tencent-finance",
            }
        return rows

    def _is_tencent_fallback_enabled(self) -> bool:
        if self._settings is None:
            return True
        return bool(getattr(self._settings, "market_overview_tencent_fallback_enabled", True))

    def _get_tencent_refresh_seconds(self) -> float:
        if self._settings is None:
            return 120.0
        return float(getattr(self._settings, "market_overview_tencent_refresh_seconds", 120))

    def _get_tencent_failure_cooldown_seconds(self) -> float:
        if self._settings is None:
            return 180.0
        return float(getattr(self._settings, "market_overview_tencent_failure_cooldown_seconds", 180))

    def _is_legu_breadth_enabled(self) -> bool:
        if self._settings is None:
            return True
        return bool(getattr(self._settings, "market_overview_legu_breadth_enabled", True))

    def _get_legu_breadth_refresh_seconds(self) -> float:
        if self._settings is None:
            return 30.0
        return float(getattr(self._settings, "market_overview_legu_breadth_refresh_seconds", 300))

    def _get_legu_breadth_timeout_seconds(self) -> float:
        if self._settings is None:
            return 10.0
        return float(getattr(self._settings, "market_overview_legu_breadth_timeout_seconds", 10))

    def _get_legu_breadth_failure_cooldown_seconds(self) -> float:
        if self._settings is None:
            return 180.0
        return float(getattr(self._settings, "market_overview_legu_breadth_failure_cooldown_seconds", 600))
