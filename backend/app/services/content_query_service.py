from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlparse

import asyncpg


CHINA_CONTENT_TZ = timezone(timedelta(hours=8))
CONTENT_FEED_OVERFETCH_MULTIPLIER = 6


def _to_iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).isoformat()
    return value.astimezone(UTC).isoformat()


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_upstream_china_datetime(value: object) -> datetime | None:
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
    return None


def _resolve_news_published_at(*, published_at: object, raw_payload: object, provider: object, upstream_source: object) -> str | None:
    if provider == "akshare" and upstream_source == "eastmoney":
        payload = _decode_jsonish(raw_payload) or {}
        repaired = _parse_upstream_china_datetime(payload.get("发布时间") or payload.get("时间"))
        if repaired is not None:
            return _to_iso(repaired)
    return _to_iso(published_at)


def _decode_jsonish(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _sanitize_public_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def _strip_summary_title_prefix(*, title: object, summary: object) -> str | None:
    normalized_summary = _safe_text(summary)
    if not normalized_summary:
        return None

    normalized_title = _safe_text(title)
    if not normalized_title:
        return normalized_summary

    bracketed_prefix = f"【{normalized_title}】"
    if normalized_summary.startswith(bracketed_prefix):
        stripped_summary = normalized_summary[len(bracketed_prefix):].strip()
        return stripped_summary or normalized_summary

    plain_prefix = f"{normalized_title}："
    if normalized_summary.startswith(plain_prefix):
        stripped_summary = normalized_summary[len(plain_prefix):].strip()
        return stripped_summary or normalized_summary

    return normalized_summary


def _normalize_news_text_for_comparison(*, title: object, value: object) -> str | None:
    normalized = _strip_summary_title_prefix(title=title, summary=value)
    if not normalized:
        return None
    normalized = re.sub(r"</?em>", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _collapse_duplicate_news_content(
    *,
    title: object,
    summary: object,
    content: object,
    raw_payload: object,
    provider: object,
    upstream_source: object,
    scope: object,
) -> str | None:
    payload = _decode_jsonish(raw_payload) or {}
    if provider == "akshare" and upstream_source == "eastmoney" and scope == "symbol":
        search_snippet = _safe_text(payload.get("新闻内容"))
        upstream_full_content = _safe_text(payload.get("内容"))
        if search_snippet and not upstream_full_content:
            return None

    normalized_content = _strip_summary_title_prefix(title=title, summary=content)
    if not normalized_content:
        return None

    normalized_summary = _normalize_news_text_for_comparison(title=title, value=summary)
    comparable_content = _normalize_news_text_for_comparison(title=title, value=normalized_content)
    if normalized_summary == normalized_content:
        return None
    if normalized_summary == comparable_content:
        return None

    return normalized_content


def _news_item_dedupe_key(item: dict[str, object]) -> tuple[str, str, str] | None:
    if item.get("type") != "news":
        return None
    source = _safe_text(item.get("url"))
    title = _safe_text(item.get("title"))
    published_at = _safe_text(item.get("publishedAt"))
    if source:
        return ("url", source, "")
    if title:
        return ("title", title, published_at or "")
    return None


def _item_sort_timestamp(item: dict[str, object]) -> datetime:
    published_at = _parse_iso_datetime(item.get("publishedAt"))
    if published_at is not None:
        return published_at
    first_seen_at = _parse_iso_datetime(item.get("firstSeenAt"))
    if first_seen_at is not None:
        return first_seen_at
    return datetime.min.replace(tzinfo=UTC)


def _item_sort_key(item: dict[str, object]) -> tuple[datetime, str, int]:
    raw_id = item.get("id")
    item_id = raw_id if isinstance(raw_id, int) else 0
    return (_item_sort_timestamp(item), str(item.get("type") or ""), item_id)


def _item_details_content(item: dict[str, object]) -> str | None:
    details = item.get("details")
    if not isinstance(details, dict):
        return None
    return _safe_text(details.get("content"))


def _select_preferred_news_item(current: dict[str, object], candidate: dict[str, object]) -> dict[str, object]:
    current_scope = str(current.get("scope") or "")
    candidate_scope = str(candidate.get("scope") or "")
    if current_scope != candidate_scope:
        if current_scope == "market":
            return candidate
        if candidate_scope == "market":
            return current

    current_ai = bool(_safe_text(current.get("aiSummary")))
    candidate_ai = bool(_safe_text(candidate.get("aiSummary")))
    if current_ai != candidate_ai:
        return candidate if candidate_ai else current

    current_detail = bool(_item_details_content(current))
    candidate_detail = bool(_item_details_content(candidate))
    if current_detail != candidate_detail:
        return candidate if candidate_detail else current

    return candidate if _item_sort_timestamp(candidate) >= _item_sort_timestamp(current) else current


def _collapse_feed_news_duplicates(items: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    news_index: dict[tuple[str, str, str], int] = {}
    for item in items:
        dedupe_key = _news_item_dedupe_key(item)
        if dedupe_key is None:
            deduped.append(item)
            continue
        existing_index = news_index.get(dedupe_key)
        if existing_index is None:
            news_index[dedupe_key] = len(deduped)
            deduped.append(item)
            continue
        deduped[existing_index] = _select_preferred_news_item(deduped[existing_index], item)
    return deduped


def _public_lane_error(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if "stock_news_em" in text:
        return "上游新闻源暂时不可用，collector 将稍后重试。"
    if "stock_notice_report" in text:
        return "上游公告源暂时不可用，collector 将稍后重试。"
    if "stock_research_report_em" in text:
        return "上游研报源暂时不可用，collector 将稍后重试。"
    return "上游内容源暂时不可用，collector 将稍后重试。"


class ContentQueryService:
    def __init__(self, dsn: str, *, lane_refresh_seconds: dict[str, int] | None = None) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._lane_refresh_seconds = lane_refresh_seconds or {
            "symbol-report": 43200,
            "symbol-news": 1800,
            "symbol-announcement": 7200,
            "market-news": 900,
        }

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            await self._ensure_runtime_schema_compatibility()

    async def _ensure_runtime_schema_compatibility(self) -> None:
        if self._pool is None:
            raise RuntimeError("ContentQueryService must be connected before schema compatibility checks")
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_research_report (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    rating TEXT,
                    institution TEXT,
                    analyst TEXT,
                    industry TEXT,
                    published_at TIMESTAMPTZ,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    source_url TEXT,
                    provider TEXT NOT NULL DEFAULT 'akshare',
                    upstream_source TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_news_item (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT,
                    scope TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    content TEXT,
                    article_source TEXT,
                    published_at TIMESTAMPTZ,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    source_url TEXT,
                    ai_summary TEXT,
                    provider TEXT NOT NULL DEFAULT 'akshare',
                    upstream_source TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_announcement_item (
                    id BIGSERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    announcement_type TEXT,
                    published_at TIMESTAMPTZ,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    pdf_url TEXT,
                    provider TEXT NOT NULL DEFAULT 'akshare',
                    upstream_source TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS content_fetch_checkpoint (
                    lane TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    cursor JSONB NOT NULL DEFAULT '{}'::jsonb,
                    next_due_at TIMESTAMPTZ NOT NULL,
                    cooldown_until TIMESTAMPTZ,
                    last_success_at TIMESTAMPTZ,
                    last_attempt_at TIMESTAMPTZ,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    PRIMARY KEY (lane, symbol)
                )
                """
            )
            await connection.execute(
                "ALTER TABLE stock_news_item ADD COLUMN IF NOT EXISTS ai_summary TEXT"
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def delete_symbol_tracking(self, symbol: str) -> None:
        if self._pool is None:
            raise RuntimeError("ContentQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            await connection.execute("DELETE FROM content_fetch_checkpoint WHERE symbol = $1", symbol)

    async def fetch_feed(
        self,
        *,
        symbol: str | None,
        content_type: str | None,
        scope: str | None,
        limit: int,
        before: datetime | None,
        published_after: datetime | None,
    ) -> list[dict[str, object]]:
        health_map = await self._fetch_lane_health_map(symbol=symbol)
        items: list[dict[str, object]] = []
        fetch_limit = limit * CONTENT_FEED_OVERFETCH_MULTIPLIER
        if content_type in {None, "report"}:
            items.extend(await self._fetch_reports(symbol=symbol, limit=fetch_limit, before=before, published_after=published_after, health_map=health_map))
        if content_type in {None, "news"}:
            items.extend(await self._fetch_news(symbol=symbol, scope=scope, limit=fetch_limit, before=before, published_after=published_after, health_map=health_map))
        if content_type in {None, "announcement"}:
            items.extend(await self._fetch_announcements(symbol=symbol, limit=fetch_limit, before=before, published_after=published_after, health_map=health_map))

        items = _collapse_feed_news_duplicates(items)
        items = self._filter_items_by_published_after(items, published_after)
        items.sort(key=_item_sort_key, reverse=True)
        return items[:limit]

    async def fetch_status(self, *, symbol: str | None) -> dict[str, object]:
        rows = await self._fetch(
            """
            SELECT lane, symbol, next_due_at, cooldown_until, last_success_at, last_attempt_at, failure_count, last_error
            FROM content_fetch_checkpoint
            WHERE ($1::text IS NULL OR symbol = $1 OR symbol = '')
            ORDER BY lane ASC, symbol ASC
            """,
            symbol,
        )
        jobs = []
        latest_ingested_at = await self._fetch_latest_ingested_at(symbol=symbol)
        summary = {"healthyJobs": 0, "degradedJobs": 0, "staleJobs": 0, "cooldownJobs": 0}
        for row in rows:
            lane_health = self._build_lane_health(row)
            if lane_health["isCoolingDown"]:
                summary["cooldownJobs"] += 1
            if lane_health["isStale"]:
                summary["staleJobs"] += 1
            if lane_health["isHealthy"]:
                summary["healthyJobs"] += 1
            else:
                summary["degradedJobs"] += 1
            jobs.append(
                {
                    "lane": row["lane"],
                    "symbol": row["symbol"] or None,
                    "nextDueAt": _to_iso(row["next_due_at"]),
                    "cooldownUntil": _to_iso(row["cooldown_until"]),
                    "lastSuccessAt": _to_iso(row["last_success_at"]),
                    "lastAttemptAt": _to_iso(row["last_attempt_at"]),
                    "failureCount": int(row["failure_count"] or 0),
                    "lastError": _public_lane_error(row["last_error"]),
                    **lane_health,
                }
            )
        return {
            "jobs": jobs,
            "latestIngestedAt": latest_ingested_at,
            "summary": summary,
        }

    async def fetch_feed_summary(
        self,
        *,
        symbol: str | None,
        content_type: str | None,
        scope: str | None,
        published_after: datetime | None,
    ) -> dict[str, int]:
        report_count = 0
        news_count = 0
        announcement_count = 0
        ai_summary_count = 0
        market_count = 0

        if content_type in {None, "report"} and scope in {None, "symbol"}:
            report_count = await self._count_reports(symbol=symbol, published_after=published_after)

        if content_type in {None, "news"}:
            news_summary = await self._count_news(symbol=symbol, scope=scope, published_after=published_after)
            news_count = news_summary["total"]
            ai_summary_count = news_summary["aiSummary"]
            market_count = news_summary["market"]

        if content_type in {None, "announcement"} and scope in {None, "symbol"}:
            announcement_count = await self._count_announcements(symbol=symbol, published_after=published_after)

        return {
            "totalItems": report_count + news_count + announcement_count,
            "reportItems": report_count,
            "newsItems": news_count,
            "announcementItems": announcement_count,
            "aiSummaryItems": ai_summary_count,
            "marketItems": market_count,
        }

    async def _count_reports(self, *, symbol: str | None, published_after: datetime | None) -> int:
        row = await self._fetchrow(
            """
            SELECT COUNT(*) AS item_count
            FROM stock_research_report
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::timestamptz IS NULL OR published_at >= $2)
            """,
            symbol,
            published_after,
        )
        return int(row["item_count"] or 0) if row is not None else 0

    async def _count_news(self, *, symbol: str | None, scope: str | None, published_after: datetime | None) -> dict[str, int]:
        row = await self._fetchrow(
            """
            SELECT COUNT(*) AS item_count,
                   COUNT(*) FILTER (WHERE NULLIF(BTRIM(ai_summary), '') IS NOT NULL) AS ai_summary_count,
                   COUNT(*) FILTER (WHERE scope = 'market') AS market_count
            FROM stock_news_item
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::text IS NULL OR scope = $2)
              AND ($3::timestamptz IS NULL OR published_at >= $3)
            """,
            symbol,
            scope,
            published_after,
        )
        if row is None:
            return {"total": 0, "aiSummary": 0, "market": 0}
        return {
            "total": int(row["item_count"] or 0),
            "aiSummary": int(row["ai_summary_count"] or 0),
            "market": int(row["market_count"] or 0),
        }

    async def _count_announcements(self, *, symbol: str | None, published_after: datetime | None) -> int:
        row = await self._fetchrow(
            """
            SELECT COUNT(*) AS item_count
            FROM stock_announcement_item
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::timestamptz IS NULL OR published_at >= $2)
            """,
            symbol,
            published_after,
        )
        return int(row["item_count"] or 0) if row is not None else 0

    async def _fetch_reports(self, *, symbol: str | None, limit: int, before: datetime | None, published_after: datetime | None, health_map: dict[tuple[str, str | None], dict[str, object]]) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT id, symbol, title, rating, institution, analyst, industry, published_at, first_seen_at, last_seen_at,
                   source_url, provider, upstream_source, metrics, raw_payload
            FROM stock_research_report
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::timestamptz IS NULL OR published_at < $2)
              AND ($3::timestamptz IS NULL OR published_at >= $3)
            ORDER BY published_at DESC NULLS LAST, first_seen_at DESC
            LIMIT $4
            """,
            symbol,
            before,
            published_after,
            limit,
        )
        return [
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "type": "report",
                "scope": "symbol",
                "title": row["title"],
                "summary": self._build_report_summary(row),
                "source": row["upstream_source"],
                "provider": row["provider"],
                "publishedAt": _to_iso(row["published_at"]),
                "firstSeenAt": _to_iso(row["first_seen_at"]),
                "lastSeenAt": _to_iso(row["last_seen_at"]),
                "url": _sanitize_public_url(row["source_url"]),
                "stale": bool(self._lookup_item_health(health_map, "symbol-report", row["symbol"]).get("isStale", False)),
                "details": {
                    "rating": row["rating"],
                    "institution": row["institution"],
                    "analyst": row["analyst"],
                    "industry": row["industry"],
                    "metrics": _decode_jsonish(row["metrics"]) or {},
                    "rawPayload": _decode_jsonish(row["raw_payload"]) or {},
                },
            }
            for row in rows
        ]

    async def _fetch_news(self, *, symbol: str | None, scope: str | None, limit: int, before: datetime | None, published_after: datetime | None, health_map: dict[tuple[str, str | None], dict[str, object]]) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT id, symbol, scope, title, summary, content, article_source, published_at, first_seen_at, last_seen_at,
                   source_url, ai_summary, provider, upstream_source, raw_payload
            FROM stock_news_item
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::text IS NULL OR scope = $2)
              AND ($3::timestamptz IS NULL OR published_at < $3)
              AND ($4::timestamptz IS NULL OR published_at >= $4)
            ORDER BY published_at DESC NULLS LAST, first_seen_at DESC
            LIMIT $5
            """,
            symbol,
            scope,
            before,
            published_after,
            limit,
        )
        items: list[dict[str, object]] = []
        for row in rows:
            raw_payload = _decode_jsonish(row["raw_payload"]) or {}
            summary = _strip_summary_title_prefix(title=row["title"], summary=row["summary"] or row["content"])
            details_content = _collapse_duplicate_news_content(
                title=row["title"],
                summary=summary,
                content=row["content"],
                raw_payload=raw_payload,
                provider=row["provider"],
                upstream_source=row["upstream_source"],
                scope=row["scope"],
            )
            items.append(
                {
                    "id": row["id"],
                    "symbol": row["symbol"],
                    "type": "news",
                    "scope": row["scope"],
                    "title": row["title"],
                    "summary": summary,
                    "aiSummary": row["ai_summary"],
                    "source": row["upstream_source"],
                    "provider": row["provider"],
                    "publishedAt": _resolve_news_published_at(
                        published_at=row["published_at"],
                        raw_payload=row["raw_payload"],
                        provider=row["provider"],
                        upstream_source=row["upstream_source"],
                    ),
                    "firstSeenAt": _to_iso(row["first_seen_at"]),
                    "lastSeenAt": _to_iso(row["last_seen_at"]),
                    "url": _sanitize_public_url(row["source_url"]),
                    "stale": bool(
                        self._lookup_item_health(
                            health_map,
                            "market-news" if row["scope"] == "market" else "symbol-news",
                            None if row["scope"] == "market" else row["symbol"],
                        ).get("isStale", False)
                    ),
                    "details": {
                        "articleSource": row["article_source"],
                        "content": details_content,
                        "rawPayload": raw_payload,
                    },
                }
            )
        return items

    async def _fetch_announcements(self, *, symbol: str | None, limit: int, before: datetime | None, published_after: datetime | None, health_map: dict[tuple[str, str | None], dict[str, object]]) -> list[dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT id, symbol, title, announcement_type, published_at, first_seen_at, last_seen_at,
                   pdf_url, provider, upstream_source, raw_payload
            FROM stock_announcement_item
            WHERE ($1::text IS NULL OR symbol = $1)
              AND ($2::timestamptz IS NULL OR published_at < $2)
              AND ($3::timestamptz IS NULL OR published_at >= $3)
            ORDER BY published_at DESC NULLS LAST, first_seen_at DESC
            LIMIT $4
            """,
            symbol,
            before,
            published_after,
            limit,
        )
        return [
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "type": "announcement",
                "scope": "symbol",
                "title": row["title"],
                "summary": row["announcement_type"],
                "source": row["upstream_source"],
                "provider": row["provider"],
                "publishedAt": _to_iso(row["published_at"]),
                "firstSeenAt": _to_iso(row["first_seen_at"]),
                "lastSeenAt": _to_iso(row["last_seen_at"]),
                "url": _sanitize_public_url(row["pdf_url"]),
                "stale": bool(self._lookup_item_health(health_map, "symbol-announcement", row["symbol"]).get("isStale", False)),
                "details": {
                    "announcementType": row["announcement_type"],
                    "rawPayload": _decode_jsonish(row["raw_payload"]) or {},
                },
            }
            for row in rows
        ]

    async def _fetch_latest_ingested_at(self, *, symbol: str | None) -> str | None:
        row = await self._fetchrow(
            """
            WITH latest_items AS (
                SELECT MAX(first_seen_at) AS latest_at FROM stock_research_report WHERE ($1::text IS NULL OR symbol = $1)
                UNION ALL
                SELECT MAX(first_seen_at) AS latest_at FROM stock_news_item WHERE ($1::text IS NULL OR symbol = $1 OR symbol IS NULL)
                UNION ALL
                SELECT MAX(first_seen_at) AS latest_at FROM stock_announcement_item WHERE ($1::text IS NULL OR symbol = $1)
            )
            SELECT MAX(latest_at) AS latest_at FROM latest_items
            """,
            symbol,
        )
        if row is None:
            return None
        return _to_iso(row["latest_at"])

    async def _fetchrow(self, query: str, *args: object) -> asyncpg.Record | None:
        if self._pool is None:
            raise RuntimeError("ContentQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("ContentQueryService must be connected before use")
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, *args)

    @staticmethod
    def _build_report_summary(row: asyncpg.Record) -> str | None:
        parts = [value for value in (row["rating"], row["institution"], row["analyst"], row["industry"]) if value]
        if not parts:
            return None
        return " · ".join(str(part) for part in parts)

    async def _fetch_lane_health_map(self, *, symbol: str | None) -> dict[tuple[str, str | None], dict[str, object]]:
        rows = await self._fetch(
            """
            SELECT lane, symbol, next_due_at, cooldown_until, last_success_at, last_attempt_at, failure_count, last_error
            FROM content_fetch_checkpoint
            WHERE ($1::text IS NULL OR symbol = $1 OR symbol = '')
            """,
            symbol,
        )
        return {(row["lane"], row["symbol"] or None): self._build_lane_health(row) for row in rows}

    def _build_lane_health(self, row: asyncpg.Record) -> dict[str, object]:
        now = datetime.now(UTC)
        lane = str(row["lane"])
        refresh_seconds = max(int(self._lane_refresh_seconds.get(lane, 1800)), 60)
        overdue_grace_seconds = max(min(refresh_seconds // 2, 1800), 300)
        last_success_at = row["last_success_at"]
        last_attempt_at = row["last_attempt_at"]
        cooldown_until = row["cooldown_until"]
        next_due_at = row["next_due_at"]
        failure_count = int(row["failure_count"] or 0)
        has_recent_failure = failure_count > 0 and (
            last_success_at is None or (last_attempt_at is not None and last_attempt_at >= last_success_at)
        )
        is_cooling_down = isinstance(cooldown_until, datetime) and cooldown_until > now
        is_overdue = (
            isinstance(next_due_at, datetime)
            and (now - next_due_at).total_seconds() > overdue_grace_seconds
            and not is_cooling_down
        )
        stale_by_age = isinstance(last_success_at, datetime) and (now - last_success_at).total_seconds() > refresh_seconds * 2
        has_public_error = bool(_public_lane_error(row["last_error"]))
        has_never_run_but_not_due = last_success_at is None and isinstance(next_due_at, datetime) and next_due_at > now
        is_stale = False if has_never_run_but_not_due else (last_success_at is None or has_recent_failure or stale_by_age or is_overdue or has_public_error)
        is_healthy = not is_stale and not is_cooling_down and not row["last_error"]
        return {
            "isHealthy": is_healthy,
            "isStale": is_stale,
            "isCoolingDown": is_cooling_down,
            "isOverdue": is_overdue,
            "refreshIntervalSeconds": refresh_seconds,
        }

    @staticmethod
    def _lookup_item_health(health_map: dict[tuple[str, str | None], dict[str, object]], lane: str, symbol: str | None) -> dict[str, object]:
        return health_map.get((lane, symbol), health_map.get((lane, None), {}))

    @staticmethod
    def _filter_items_by_published_after(items: list[dict[str, object]], published_after: datetime | None) -> list[dict[str, object]]:
        if published_after is None:
            return items

        filtered_items: list[dict[str, object]] = []
        for item in items:
            published_at = _parse_iso_datetime(item.get("publishedAt"))
            fallback_seen_at = _parse_iso_datetime(item.get("firstSeenAt"))
            effective_time = published_at or fallback_seen_at
            if effective_time is None or effective_time < published_after:
                continue
            filtered_items.append(item)
        return filtered_items
