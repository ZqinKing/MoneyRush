from __future__ import annotations

import hashlib
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import akshare as ak


logger = logging.getLogger(__name__)


class UpstreamContentFetchError(RuntimeError):
    pass


def _safe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)

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
        return parsed.replace(tzinfo=UTC)
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
            published_at = _safe_datetime(row.get("日期") or row.get("发布日期"))
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

        try:
            frame = self._call(ak.stock_news_em, symbol=symbol)
        except Exception as exc:
            raise UpstreamContentFetchError(f"stock_news_em failed for {symbol}: {exc}") from exc

        if frame is None or getattr(frame, "empty", True):
            return FetchResult(items=items, upstream_source="eastmoney", fetched_at=fetched_at)

        for row in frame.to_dict(orient="records"):
            title = _safe_text(row.get("新闻标题") or row.get("标题"))
            if not title:
                continue
            keyword = _safe_text(row.get("关键词"))
            if keyword and keyword != symbol:
                logger.warning("skip mismatched stock news item", extra={"symbol": symbol, "keyword": keyword, "title": title})
                continue
            published_at = _safe_datetime(row.get("发布时间") or row.get("时间"))
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
                published_at = _safe_datetime(row.get("公告时间") or row.get("公告日期") or row.get("时间"))
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
            published_at = _safe_datetime(row.get("时间") or row.get("发布时间"))
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
