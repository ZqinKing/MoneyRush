from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request


router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("/events")
async def timeline_events(
    request: Request,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    category: str | None = None,
    level: str | None = None,
) -> dict[str, object]:
    normalized_category = category.strip().lower() if isinstance(category, str) and category.strip() else None
    normalized_level = level.strip().lower() if isinstance(level, str) and level.strip() else None
    return {
        "events": await request.app.state.timeline_query_service.fetch_events(
            from_date=from_date,
            to_date=to_date,
            category=normalized_category,
            level=normalized_level,
        ),
        "displayTimezone": "Asia/Shanghai",
    }
