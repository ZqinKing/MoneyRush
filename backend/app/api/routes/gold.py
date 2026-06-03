from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request


router = APIRouter(prefix="/gold", tags=["gold"])
GOLD_NEWS_RE = re.compile(r"黄金|金价|贵金属|伦敦金|沪金|Au\(T\+D\)|AU|XAU", re.IGNORECASE)
GOLD_FUND_RE = re.compile(r"黄金|贵金属", re.IGNORECASE)
GOLD_QUOTE_DEFAULTS = {
    "au0": {
        "id": "au0",
        "code": "nf_AU0",
        "name": "沪金主力",
        "market": "SHFE",
        "price": None,
        "open": None,
        "high": None,
        "low": None,
        "changePct": None,
        "updatedAt": None,
        "currency": "CNY",
        "source": "sina:nf_AU0",
        "degraded": True,
        "stale": True,
        "sortOrder": 1,
    },
    "autd": {
        "id": "autd",
        "code": "Au(T+D)",
        "name": "上海金 T+D",
        "market": "SGE",
        "price": None,
        "open": None,
        "high": None,
        "low": None,
        "changePct": None,
        "updatedAt": None,
        "currency": "CNY",
        "source": "akshare-sge:Au(T+D)",
        "degraded": True,
        "stale": True,
        "sortOrder": 2,
    },
    "xau": {
        "id": "xau",
        "code": "hf_XAU",
        "name": "伦敦金（现货黄金）",
        "market": "LONDON",
        "price": None,
        "open": None,
        "high": None,
        "low": None,
        "changePct": None,
        "updatedAt": None,
        "currency": "USD",
        "source": "sina:hf_XAU",
        "degraded": True,
        "stale": True,
        "sortOrder": 3,
    },
    "518880": {
        "id": "518880",
        "code": "sh518880",
        "name": "黄金ETF",
        "market": "SSE",
        "price": None,
        "open": None,
        "high": None,
        "low": None,
        "changePct": None,
        "updatedAt": None,
        "currency": "CNY",
        "source": "tencent:sh518880",
        "degraded": True,
        "stale": True,
        "sortOrder": 4,
    },
}


def _public_source_payload(source_payload: dict[str, object]) -> dict[str, object]:
    return {
        **source_payload,
        "error": "source unavailable" if source_payload.get("error") else None,
    }


def _is_gold_fund(snapshot: dict[str, object]) -> bool:
    for key in ("fundName", "fundType", "benchmarkIndex"):
        value = snapshot.get(key)
        if isinstance(value, str) and GOLD_FUND_RE.search(value):
            return True
    return False


@router.get("/dashboard")
async def gold_dashboard(request: Request) -> dict[str, object]:
    redis_store = request.app.state.redis_store
    fund_query_service = request.app.state.fund_query_service
    content_query_service = request.app.state.content_query_service

    cached_payload = await redis_store.get_gold_dashboard()
    active_funds = await redis_store.get_active_funds()
    fund_snapshots = await redis_store.get_fund_snapshots(active_funds)
    query_snapshots = await fund_query_service.fetch_active_fund_snapshots(active_funds)

    merged_fund_snapshots = {**fund_snapshots}
    for fund_code, query_snapshot in query_snapshots.items():
        if fund_code in merged_fund_snapshots:
            merged_fund_snapshots[fund_code] = {
                **merged_fund_snapshots[fund_code],
                **query_snapshot,
            }
        else:
            merged_fund_snapshots[fund_code] = query_snapshot

    gold_funds = []
    for fund_code, snapshot in merged_fund_snapshots.items():
        if not isinstance(snapshot, dict):
            continue
        normalized_snapshot = {"fundCode": fund_code, **snapshot}
        if _is_gold_fund(normalized_snapshot):
            gold_funds.append(normalized_snapshot)
    gold_funds.sort(key=lambda item: str(item.get("fundCode") or ""))

    published_after = datetime.now(UTC) - timedelta(days=7)
    news_items = await content_query_service.fetch_feed(
        symbol=None,
        content_type="news",
        scope="market",
        limit=80,
        before=None,
        published_after=published_after,
    )
    gold_news = [item for item in news_items if isinstance(item.get("title"), str) and GOLD_NEWS_RE.search(item["title"])]

    normalized_quotes = {quote_id: dict(default_quote) for quote_id, default_quote in GOLD_QUOTE_DEFAULTS.items()}
    normalized_sources = {
        quote_id: {
            "id": quote_id,
            "status": "error",
            "updatedAt": default_quote.get("updatedAt"),
            "source": default_quote.get("source"),
            "error": None,
        }
        for quote_id, default_quote in GOLD_QUOTE_DEFAULTS.items()
    }

    if isinstance(cached_payload, dict):
        for quote in cached_payload.get("quotes", []):
            if not isinstance(quote, dict):
                continue
            quote_id = str(quote.get("id") or "")
            if quote_id in normalized_quotes:
                normalized_quotes[quote_id] = {
                    **normalized_quotes[quote_id],
                    **quote,
                }
        for quote_id, source_payload in (cached_payload.get("sources", {}) or {}).items():
            if quote_id in normalized_sources and isinstance(source_payload, dict):
                normalized_sources[quote_id] = _public_source_payload({
                    **normalized_sources[quote_id],
                    **source_payload,
                })

    if cached_payload is None:
        return {
            "generatedAt": None,
            "isTradingSession": False,
            "quotes": list(normalized_quotes.values()),
            "sources": normalized_sources,
            "degraded": True,
            "funds": gold_funds,
            "news": gold_news[:12],
        }

    return {
        "generatedAt": cached_payload.get("generatedAt"),
        "isTradingSession": bool(cached_payload.get("isTradingSession", False)),
        "quotes": list(normalized_quotes.values()),
        "sources": normalized_sources,
        "degraded": bool(cached_payload.get("degraded", False)) or any(source.get("status") != "ok" for source in normalized_sources.values()),
        "funds": gold_funds,
        "news": gold_news[:12],
    }
