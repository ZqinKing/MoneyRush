from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import akshare as ak
import requests

from collector.services.tencent_quote_client import TencentQuoteClient


logger = logging.getLogger(__name__)
CHINA_CONTENT_TZ = timezone(timedelta(hours=8))
SYMBOL_NEWS_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
SYMBOL_NEWS_CALLBACK = "cb"
SYMBOL_NEWS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Referer": "https://so.eastmoney.com/news/",
}


class UpstreamContentFetchError(RuntimeError):
    pass


def _safe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_datetime(value: object, *, anchor: datetime | None = None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=CHINA_CONTENT_TZ)
        return normalized.astimezone(UTC)

    text = _safe_text(value)
    if not text:
        return None

    normalized = text.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d",
    )
    for fmt in candidates:
        try:
            parsed = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=CHINA_CONTENT_TZ).astimezone(UTC)

    time_only_formats = ("%H:%M:%S", "%H:%M")
    for fmt in time_only_formats:
        try:
            parsed_time = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        anchor_dt = anchor or datetime.now(UTC)
        anchor_china = anchor_dt.astimezone(CHINA_CONTENT_TZ)
        parsed = anchor_china.replace(
            hour=parsed_time.hour,
            minute=parsed_time.minute,
            second=parsed_time.second,
            microsecond=0,
        )
        return parsed.astimezone(UTC)
    return None


def _dedupe_key(*parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class FetchResult:
    items: list[dict[str, object]]
    upstream_source: str
    fetched_at: datetime
    warning_message: str | None = None


class AkshareContentClient:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._last_request_at: float | None = None
        self._tencent_quote_client = TencentQuoteClient()
        self._symbol_news_keyword_cache: dict[str, str | None] = {}

    def _sleep_for_rate_limit(self) -> None:
        min_interval = max(self._settings.content_fetch_min_interval_seconds, 0)
        jitter = max(self._settings.content_fetch_jitter_seconds, 0)
        now = time.monotonic()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            wait_seconds = min_interval - elapsed
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        if jitter > 0:
            time.sleep(random.uniform(0, jitter))
        self._last_request_at = time.monotonic()

    def _call(self, func: Callable[..., object], *args: object, **kwargs: object):
        self._sleep_for_rate_limit()
        return func(*args, **kwargs)

    def fetch_research_reports(self, symbol: str) -> FetchResult:
        frame = self._call(ak.stock_research_report_em, symbol=symbol)
        fetched_at = datetime.now(UTC)
        items: list[dict[str, object]] = []

        if frame is None or getattr(frame, "empty", True):
            return FetchResult(items=items, upstream_source="eastmoney", fetched_at=fetched_at)

        for row in frame.to_dict(orient="records"):
            title = _safe_text(row.get("研报名称"))
            if not title:
                continue
            published_at = _safe_datetime(row.get("日期") or row.get("发布日期"), anchor=fetched_at)
            items.append(
                {
                    "symbol": symbol,
                    "title": title,
                    "rating": _safe_text(row.get("评级")),
                    "institution": _safe_text(row.get("机构")) or _safe_text(row.get("评级机构")),
                    "analyst": _safe_text(row.get("分析师")),
                    "industry": _safe_text(row.get("行业")),
                    "publishedAt": published_at or fetched_at,
                    "firstSeenAt": fetched_at,
                    "lastSeenAt": fetched_at,
                    "sourceUrl": None,
                    "provider": "akshare",
                    "upstreamSource": "eastmoney",
                    "dedupeKey": _dedupe_key("research", symbol, title, published_at),
                    "metrics": {
                        key: value
                        for key, value in row.items()
                        if key not in {"研报名称", "评级", "机构", "评级机构", "分析师", "行业", "日期", "发布日期"}
                    },
                    "rawPayload": row,
                }
            )

        return FetchResult(items=items, upstream_source="eastmoney", fetched_at=fetched_at)

    def fetch_symbol_news(self, symbol: str) -> FetchResult:
        fetched_at = datetime.now(UTC)
        items: list[dict[str, object]] = []

        keyword_candidates = self._build_symbol_news_keywords(symbol)
        records: list[dict[str, object]] | None = None
        last_error: Exception | None = None

        for keyword in keyword_candidates:
            try:
                candidate_records = self._fetch_symbol_news_records(keyword)
                if not candidate_records:
                    continue
                records = candidate_records
                if records is not None:
                    break
            except Exception as exc:
                last_error = exc
                logger.warning("stock_news_em candidate failed", extra={"symbol": symbol, "keyword": keyword, "error": str(exc)})

        if records is None and last_error is not None:
            raise UpstreamContentFetchError(f"stock_news_em failed for {symbol}: {last_error}") from last_error

        if records is None:
            return FetchResult(items=items, upstream_source="eastmoney", fetched_at=fetched_at)

        accepted_keywords = {value for value in keyword_candidates if value}
        for row in records:
            title = _safe_text(row.get("新闻标题") or row.get("标题"))
            if not title:
                continue
            keyword = _safe_text(row.get("关键词"))
            if keyword and accepted_keywords and keyword not in accepted_keywords:
                logger.warning("skip mismatched stock news item", extra={"symbol": symbol, "keyword": keyword, "title": title})
                continue
            published_at = _safe_datetime(row.get("发布时间") or row.get("时间"), anchor=fetched_at)
            source_url = _safe_text(row.get("新闻链接") or row.get("链接"))
            items.append(
                {
                    "symbol": symbol,
                    "scope": "symbol",
                    "title": title,
                    "summary": _safe_text(row.get("新闻内容") or row.get("摘要")),
                    "content": _safe_text(row.get("新闻内容") or row.get("内容")),
                    "articleSource": _safe_text(row.get("文章来源") or row.get("新闻来源") or row.get("来源")),
                    "publishedAt": published_at or fetched_at,
                    "firstSeenAt": fetched_at,
                    "lastSeenAt": fetched_at,
                    "sourceUrl": source_url,
                    "provider": "akshare",
                    "upstreamSource": "eastmoney",
                    "dedupeKey": _dedupe_key("news", symbol, title, published_at, source_url),
                    "rawPayload": row,
                }
            )

        max_items = max(self._settings.content_news_backfill_max_items, 1)
        return FetchResult(items=items[:max_items], upstream_source="eastmoney", fetched_at=fetched_at)

    def _fetch_symbol_news_records(self, keyword: str) -> list[dict[str, object]]:
        self._sleep_for_rate_limit()
        response = requests.get(
            SYMBOL_NEWS_SEARCH_URL,
            params={
                "cb": SYMBOL_NEWS_CALLBACK,
                "param": json.dumps(
                    {
                        "uid": "",
                        "keyword": keyword,
                        "type": ["cmsArticle"],
                        "client": "web",
                        "clientType": "web",
                        "clientVersion": "curr",
                        "param": {
                            "cmsArticle": {
                                "searchScope": "default",
                                "sort": "default",
                                "pageIndex": 1,
                                "pageSize": 100,
                                "preTag": "<em>",
                                "postTag": "</em>",
                            }
                        },
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
            headers=SYMBOL_NEWS_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        payload_text = response.text.strip()
        if not payload_text:
            raise UpstreamContentFetchError(f"empty stock news response for keyword {keyword}")

        callback_prefix = f"{SYMBOL_NEWS_CALLBACK}("
        if not payload_text.startswith(callback_prefix) or not payload_text.endswith(")"):
            raise UpstreamContentFetchError(f"unexpected stock news payload for keyword {keyword}")

        payload = json.loads(payload_text[len(callback_prefix):-1])
        result = payload.get("result") if isinstance(payload, dict) else None
        items = result.get("cmsArticle") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise UpstreamContentFetchError(f"missing cmsArticle result for keyword {keyword}")

        normalized_items: list[dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            article_code = _safe_text(item.get("code"))
            normalized_items.append(
                {
                    "关键词": keyword,
                    "新闻标题": _safe_text(item.get("title")),
                    "新闻内容": _safe_text(item.get("content")),
                    "发布时间": _safe_text(item.get("date")),
                    "文章来源": _safe_text(item.get("mediaName")),
                    "新闻链接": f"https://finance.eastmoney.com/a/{article_code}.html" if article_code else None,
                }
            )
        return normalized_items

    def _build_symbol_news_keywords(self, symbol: str) -> list[str]:
        company_name = self._resolve_symbol_company_name(symbol)
        candidates = [company_name, symbol]
        deduped: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in deduped:
                deduped.append(candidate)
        return deduped or [symbol]

    def _resolve_symbol_company_name(self, symbol: str) -> str | None:
        if symbol in self._symbol_news_keyword_cache:
            return self._symbol_news_keyword_cache[symbol]

        try:
            quote = self._tencent_quote_client.fetch_quote(symbol)
        except Exception:
            logger.exception("failed to resolve symbol company name for content fetch", extra={"symbol": symbol})
            self._symbol_news_keyword_cache[symbol] = None
            return None

        company_name = _safe_text(quote.company_name)
        self._symbol_news_keyword_cache[symbol] = company_name
        return company_name

    def fetch_announcements(self, symbol: str) -> FetchResult:
        fetched_at = datetime.now(UTC)
        items: list[dict[str, object]] = []
        max_days = max(self._settings.content_announcement_backfill_pages, 1)
        max_items = max_days * 50
        seen_keys: set[str] = set()
        successful_fetch = False
        last_error: Exception | None = None

        for day_offset in range(max_days):
            query_date = (fetched_at - timedelta(days=day_offset)).strftime("%Y%m%d")
            try:
                frame = self._call(ak.stock_notice_report, symbol="全部", date=query_date)
                successful_fetch = True
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "stock_notice_report fetch failed",
                    extra={"symbol": symbol, "date": query_date, "error": str(exc)},
                )
                continue

            if frame is None or getattr(frame, "empty", True):
                continue

            for row in frame.to_dict(orient="records"):
                row_symbol = _safe_text(row.get("代码") or row.get("股票代码") or row.get("证券代码"))
                if row_symbol and row_symbol != symbol:
                    continue
                title = _safe_text(row.get("公告标题") or row.get("标题"))
                if not title:
                    continue
                published_at = _safe_datetime(row.get("公告时间") or row.get("公告日期") or row.get("时间"), anchor=fetched_at)
                pdf_url = _safe_text(row.get("公告链接") or row.get("PDF链接") or row.get("链接") or row.get("网址"))
                dedupe_key = _dedupe_key("announcement", symbol, title, published_at, pdf_url)
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                items.append(
                    {
                        "symbol": symbol,
                        "title": title,
                        "announcementType": _safe_text(row.get("公告类型") or row.get("类型")),
                        "publishedAt": published_at or fetched_at,
                        "firstSeenAt": fetched_at,
                        "lastSeenAt": fetched_at,
                        "pdfUrl": pdf_url,
                        "provider": "akshare",
                        "upstreamSource": "eastmoney",
                        "dedupeKey": dedupe_key,
                        "rawPayload": row,
                    }
                )
                if len(items) >= max_items:
                    return FetchResult(
                        items=items,
                        upstream_source="eastmoney",
                        fetched_at=fetched_at,
                        warning_message=f"stock_notice_report partial failure for {symbol}: {last_error}" if last_error else None,
                    )

        if not successful_fetch and last_error is not None:
            raise UpstreamContentFetchError(f"stock_notice_report failed for {symbol}: {last_error}") from last_error

        return FetchResult(
            items=items,
            upstream_source="eastmoney",
            fetched_at=fetched_at,
            warning_message=f"stock_notice_report partial failure for {symbol}: {last_error}" if last_error else None,
        )

    def fetch_market_news(self) -> FetchResult:
        frame = self._call(ak.stock_info_global_em)
        fetched_at = datetime.now(UTC)
        items: list[dict[str, object]] = []

        if frame is None or getattr(frame, "empty", True):
            return FetchResult(items=items, upstream_source="eastmoney", fetched_at=fetched_at)

        for row in frame.to_dict(orient="records"):
            title = _safe_text(row.get("标题"))
            if not title:
                continue
            published_at = _safe_datetime(row.get("时间") or row.get("发布时间"), anchor=fetched_at)
            source_url = _safe_text(row.get("链接"))
            items.append(
                {
                    "symbol": None,
                    "scope": "market",
                    "title": title,
                    "summary": _safe_text(row.get("摘要") or row.get("内容")),
                    "content": _safe_text(row.get("内容") or row.get("摘要")),
                    "articleSource": "东方财富全球财经快讯",
                    "publishedAt": published_at or fetched_at,
                    "firstSeenAt": fetched_at,
                    "lastSeenAt": fetched_at,
                    "sourceUrl": source_url,
                    "provider": "akshare",
                    "upstreamSource": "eastmoney",
                    "dedupeKey": _dedupe_key("market-news", title, published_at, source_url),
                    "rawPayload": row,
                }
            )

        return FetchResult(items=items[:50], upstream_source="eastmoney", fetched_at=fetched_at)

    def initial_due_map(self) -> dict[str, datetime]:
        now = datetime.now(UTC)
        return {
            "symbol-report": now,
            "symbol-news": now,
            "symbol-announcement": now,
            "market-news": now + timedelta(seconds=self._settings.content_market_news_refresh_seconds),
        }
