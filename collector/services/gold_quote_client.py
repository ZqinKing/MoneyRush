from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone
import logging
from time import monotonic
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import akshare as ak
import requests


logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
}
TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://stockapp.finance.qq.com/",
}
GOLD_NEWS_KEYWORDS = ("黄金", "金价", "贵金属", "伦敦金", "沪金", "Au(T+D)", "AU", "XAU")
ALLOWED_GOLD_SOURCE_URLS = {
    "au0": ("hq.sinajs.cn", "/list=nf_AU0"),
    "xau": ("hq.sinajs.cn", "/list=hf_XAU"),
    "etf": ("qt.gtimg.cn", "/q=sh518880"),
}


def _validate_gold_source_url(source_id: str, url: str) -> str:
    allowed = ALLOWED_GOLD_SOURCE_URLS[source_id]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != allowed[0] or parsed.path != allowed[1] or parsed.query:
        raise ValueError(f"invalid configured gold source URL for {source_id}")
    return url


def _to_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text or text == "--":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _build_change_pct(price: float | None, previous_close: float | None) -> float | None:
    if price is None or previous_close in (None, 0):
        return None
    return round(((price - previous_close) / previous_close) * 100, 4)


def _build_iso_datetime(date_text: str | None, time_text: str | None) -> str | None:
    if not date_text:
        return None
    normalized_time = (time_text or "00:00:00").strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H%M%S"):
        try:
            parsed = datetime.strptime(f"{date_text.strip()} {normalized_time}", pattern)
            return parsed.replace(tzinfo=CHINA_TZ).astimezone(UTC).isoformat()
        except ValueError:
            continue
    return None


def _is_weekday_china(now: datetime) -> bool:
    return now.astimezone(CHINA_TZ).weekday() < 5


def is_gold_trading_session(now: datetime | None = None) -> bool:
    current = now.astimezone(CHINA_TZ) if now is not None else datetime.now(CHINA_TZ)
    if current.weekday() >= 5:
        return False
    current_time = current.time()
    if time(9, 0) <= current_time <= time(15, 30):
        return True
    if current_time >= time(20, 0):
        return True
    if current_time <= time(2, 30):
        return True
    return False


class GoldQuoteClient:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._sge_history_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}

    def fetch(self, previous_payload: dict[str, object] | None = None) -> dict[str, object]:
        generated_at = datetime.now(UTC)
        quotes: list[dict[str, object]] = []
        sources: dict[str, dict[str, object]] = {}
        previous_quotes = self._index_previous_quotes(previous_payload)
        previous_sources = self._index_previous_sources(previous_payload)

        for source_id, enabled, loader in (
            ("au0", bool(self._settings.gold_au0_enabled), self._fetch_au0_quote),
            ("autd", bool(self._settings.gold_autd_enabled), self._fetch_autd_quote),
            ("xau", bool(self._settings.gold_xau_enabled), self._fetch_xau_quote),
            ("518880", bool(self._settings.gold_etf_enabled), self._fetch_etf_quote),
        ):
            if not enabled:
                sources[source_id] = {
                    "id": source_id,
                    "status": "disabled",
                    "updatedAt": previous_sources.get(source_id, {}).get("updatedAt"),
                    "source": previous_sources.get(source_id, {}).get("source"),
                    "error": None,
                }
                previous_quote = previous_quotes.get(source_id)
                if previous_quote:
                    quote = {**previous_quote, "degraded": True, "stale": True}
                    quotes.append(quote)
                continue

            try:
                quote = loader()
            except Exception as exc:
                logger.exception("gold source fetch failed", extra={"source_id": source_id})
                previous_quote = previous_quotes.get(source_id)
                previous_source = previous_sources.get(source_id, {})
                if previous_quote:
                    quote = {**previous_quote, "degraded": True, "stale": True}
                    quotes.append(quote)
                sources[source_id] = {
                    "id": source_id,
                    "status": "stale" if previous_quote else "error",
                    "updatedAt": previous_source.get("updatedAt") or (previous_quote or {}).get("updatedAt"),
                    "source": previous_source.get("source") or (previous_quote or {}).get("source"),
                    "error": "source unavailable",
                }
                continue

            quotes.append({**quote, "degraded": bool(quote.get("degraded", False)), "stale": bool(quote.get("stale", False))})
            sources[source_id] = {
                "id": source_id,
                "status": "stale" if quote.get("stale") else "ok",
                "updatedAt": quote.get("updatedAt"),
                "source": quote.get("source"),
                "error": None,
            }

        quotes.sort(key=lambda item: str(item.get("sortOrder", item.get("id", ""))))
        degraded = any(source.get("status") != "ok" for source in sources.values())
        return {
            "generatedAt": generated_at.isoformat(),
            "isTradingSession": is_gold_trading_session(generated_at),
            "quotes": quotes,
            "sources": sources,
            "degraded": degraded,
        }

    def next_refresh_seconds(self) -> int:
        now = datetime.now(UTC)
        if is_gold_trading_session(now):
            return max(int(self._settings.gold_dashboard_refresh_seconds), 1)
        return max(int(self._settings.gold_dashboard_offsession_refresh_seconds), 1)

    @staticmethod
    def _index_previous_quotes(previous_payload: dict[str, object] | None) -> dict[str, dict[str, object]]:
        previous_quotes = previous_payload.get("quotes") if isinstance(previous_payload, dict) else None
        if not isinstance(previous_quotes, list):
            return {}
        return {
            str(item.get("id")): item
            for item in previous_quotes
            if isinstance(item, dict) and item.get("id")
        }

    @staticmethod
    def _index_previous_sources(previous_payload: dict[str, object] | None) -> dict[str, dict[str, object]]:
        previous_sources = previous_payload.get("sources") if isinstance(previous_payload, dict) else None
        if not isinstance(previous_sources, dict):
            return {}
        return {
            str(key): value
            for key, value in previous_sources.items()
            if isinstance(value, dict)
        }

    def _fetch_sina_payload(self, url: str) -> str:
        request = Request(url, headers=SINA_HEADERS)
        with urlopen(request, timeout=10) as response:
            payload = response.read().decode("gbk", errors="replace").strip()
        if not payload:
            raise ValueError("empty sina payload")
        return payload

    def _fetch_au0_quote(self) -> dict[str, object]:
        payload = self._fetch_sina_payload(_validate_gold_source_url("au0", self._settings.gold_au0_url))
        _, quoted_value = payload.split('="', 1)
        raw = quoted_value.removesuffix('";').split(",")
        if len(raw) < 18 or not raw[0]:
            raise ValueError("unexpected AU0 payload shape")
        price = _to_float(raw[8])
        previous_settle = _to_float(raw[10])
        if price is None:
            raise ValueError("AU0 price missing")
        return {
            "id": "au0",
            "code": "nf_AU0",
            "name": raw[0],
            "market": "SHFE",
            "price": price,
            "open": _to_float(raw[2]),
            "high": _to_float(raw[3]),
            "low": _to_float(raw[4]),
            "changePct": _build_change_pct(price, previous_settle),
            "updatedAt": _build_iso_datetime(raw[17], raw[1]) or datetime.now(UTC).isoformat(),
            "currency": "CNY",
            "source": "sina:nf_AU0",
            "sortOrder": 1,
        }

    def _fetch_xau_quote(self) -> dict[str, object]:
        payload = self._fetch_sina_payload(_validate_gold_source_url("xau", self._settings.gold_xau_url))
        _, quoted_value = payload.split('="', 1)
        raw = quoted_value.removesuffix('";').split(",")
        if len(raw) < 14:
            raise ValueError("unexpected XAU payload shape")
        price = _to_float(raw[0])
        previous_settle = _to_float(raw[1])
        if price is None:
            raise ValueError("XAU price missing")
        return {
            "id": "xau",
            "code": "hf_XAU",
            "name": raw[13] or "伦敦金",
            "market": "LONDON",
            "price": price,
            "open": _to_float(raw[2]),
            "high": _to_float(raw[4]),
            "low": _to_float(raw[5]),
            "changePct": _build_change_pct(price, previous_settle),
            "updatedAt": _build_iso_datetime(raw[12], raw[6]) or datetime.now(UTC).isoformat(),
            "currency": "USD",
            "source": "sina:hf_XAU",
            "sortOrder": 3,
        }

    def _fetch_etf_quote(self) -> dict[str, object]:
        request = requests.get(_validate_gold_source_url("etf", self._settings.gold_etf_url), headers=TENCENT_HEADERS, timeout=10)
        request.raise_for_status()
        payload = request.content.decode("gbk", errors="replace").strip()
        if not payload or '="' not in payload:
            raise ValueError("unexpected ETF payload shape")
        _, quoted_value = payload.split('="', 1)
        raw = quoted_value.removesuffix('";').split("~")
        if len(raw) < 35:
            raise ValueError("unexpected ETF field count")
        price = _to_float(raw[3])
        if price is None:
            raise ValueError("ETF price missing")
        timestamp = raw[30] if len(raw) > 30 else ""
        updated_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ).astimezone(UTC).isoformat() if timestamp else datetime.now(UTC).isoformat()
        return {
            "id": "518880",
            "code": f"sh{raw[2]}",
            "name": raw[1] or "黄金ETF",
            "market": "SSE",
            "price": price,
            "open": _to_float(raw[5]),
            "high": _to_float(raw[33]),
            "low": _to_float(raw[34]),
            "changePct": _to_float(raw[32]),
            "updatedAt": updated_at,
            "currency": raw[82] if len(raw) > 82 and raw[82] else "CNY",
            "source": "tencent:sh518880",
            "sortOrder": 4,
        }

    def _fetch_autd_quote(self) -> dict[str, object]:
        history_rows = self._get_sge_history_rows("Au(T+D)")
        previous_close = None
        latest_history = history_rows[-1] if history_rows else None
        if isinstance(latest_history, dict):
            previous_close = _to_float(latest_history.get("close"))

        try:
            quote_frame = ak.spot_quotations_sge(symbol="Au(T+D)")
        except Exception:
            logger.warning("SGE Au(T+D) quotation failed; falling back to latest history", exc_info=True)
            return self._build_autd_history_fallback(latest_history)

        if quote_frame is None or getattr(quote_frame, "empty", True):
            return self._build_autd_history_fallback(latest_history)

        latest_row = quote_frame.iloc[-1]
        price = _to_float(latest_row.get("现价"))
        if price is None:
            return self._build_autd_history_fallback(latest_history)
        updated_at_text = str(latest_row.get("更新时间") or "").strip()
        updated_at = self._parse_akshare_update_time(updated_at_text)
        return {
            "id": "autd",
            "code": "Au(T+D)",
            "name": "上海金 T+D",
            "market": "SGE",
            "price": price,
            "open": _to_float((latest_history or {}).get("open")) if latest_history else None,
            "high": _to_float((latest_history or {}).get("high")) if latest_history else None,
            "low": _to_float((latest_history or {}).get("low")) if latest_history else None,
            "changePct": _build_change_pct(price, previous_close),
            "updatedAt": updated_at or datetime.now(UTC).isoformat(),
            "currency": "CNY",
            "source": "akshare-sge:Au(T+D)",
            "degraded": False,
            "stale": False,
            "sortOrder": 2,
        }

    def _build_autd_history_fallback(self, latest_history: dict[str, object] | None) -> dict[str, object]:
        if latest_history is None:
            raise ValueError("Au(T+D) quotation unavailable and history fallback empty")
        close_price = _to_float(latest_history.get("close"))
        if close_price is None:
            raise ValueError("Au(T+D) history fallback missing close")
        history_date = latest_history.get("date")
        updated_at = datetime.now(UTC)
        if history_date is not None:
            try:
                parsed_date = datetime.fromisoformat(str(history_date))
                updated_at = parsed_date.replace(tzinfo=CHINA_TZ).astimezone(UTC)
            except ValueError:
                updated_at = datetime.now(UTC)
        return {
            "id": "autd",
            "code": "Au(T+D)",
            "name": "上海金 T+D",
            "market": "SGE",
            "price": close_price,
            "open": _to_float(latest_history.get("open")),
            "high": _to_float(latest_history.get("high")),
            "low": _to_float(latest_history.get("low")),
            "changePct": None,
            "updatedAt": updated_at.isoformat(),
            "currency": "CNY",
            "source": "akshare-sge-history:Au(T+D)",
            "degraded": True,
            "stale": True,
            "sortOrder": 2,
        }

    def _get_sge_history_rows(self, symbol: str) -> list[dict[str, object]]:
        cache_entry = self._sge_history_cache.get(symbol)
        now = monotonic()
        if cache_entry and now - cache_entry[0] < 300:
            return cache_entry[1]
        frame = ak.spot_hist_sge(symbol=symbol)
        rows = frame.to_dict("records") if frame is not None and not getattr(frame, "empty", True) else []
        self._sge_history_cache[symbol] = (now, rows)
        return rows

    @staticmethod
    def _parse_akshare_update_time(value: str) -> str | None:
        if not value:
            return None
        for pattern in ("%Y年%m月%d日 %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(value, pattern)
                return parsed.replace(tzinfo=CHINA_TZ).astimezone(UTC).isoformat()
            except ValueError:
                continue
        return None
