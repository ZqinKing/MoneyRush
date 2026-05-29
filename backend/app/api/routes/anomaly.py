from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request, status


router = APIRouter(prefix="/anomaly", tags=["anomaly"])
ALLOWED_SEVERITIES = {"critical", "high", "medium"}
ALLOWED_SORT_KEYS = {"time", "magnitude", "relevance"}


def _parse_severities(values: list[str] | None) -> set[str]:
    if not values:
        return {"critical", "high"}

    severities: set[str] = set()
    for value in values:
        for item in value.split(","):
            normalized = item.strip().lower()
            if normalized:
                severities.add(normalized)

    invalid = severities - ALLOWED_SEVERITIES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unsupported severity: {', '.join(sorted(invalid))}",
        )
    return severities or {"critical", "high"}


@router.get("/daily")
async def daily_anomaly_report(
    request: Request,
    report_date: date | None = Query(default=None, alias="date"),
    severity: list[str] | None = Query(default=None),
    portfolio_only: bool = False,
    sort_by: str = "relevance",
) -> dict[str, object]:
    if sort_by not in ALLOWED_SORT_KEYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"sort_by must be one of: {', '.join(sorted(ALLOWED_SORT_KEYS))}",
        )

    redis_store = request.app.state.redis_store
    query_service = request.app.state.market_detail_query_service
    symbols = await redis_store.get_active_symbols()
    active_funds = await redis_store.get_active_funds()
    return await query_service.fetch_daily_anomaly_report(
        symbols=symbols,
        active_funds=active_funds,
        report_date=report_date.isoformat() if report_date is not None else None,
        severities=_parse_severities(severity),
        portfolio_only=portfolio_only,
        sort_by=sort_by,
    )
