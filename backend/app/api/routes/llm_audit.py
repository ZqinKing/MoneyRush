from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.services.llm_capabilities import get_llm_feature_capabilities


router = APIRouter(prefix="/llm-audit", tags=["llm-audit"])
CHINA_TZ = timezone(timedelta(hours=8))


def _resolve_target_date(value: str | None) -> date:
    if value is None:
        return datetime.now(CHINA_TZ).date()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="date must be YYYY-MM-DD") from exc


@router.get("/capabilities")
async def llm_audit_capabilities(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return get_llm_feature_capabilities(settings)


@router.get("/daily")
async def llm_audit_daily(request: Request, date: str | None = Query(default=None)) -> dict[str, object]:
    settings = request.app.state.settings
    capabilities = get_llm_feature_capabilities(settings)
    if not capabilities["enabled"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="llm_audit_unavailable")

    target_date = _resolve_target_date(date)
    payload = await request.app.state.llm_audit_query_service.fetch_daily_summary(target_date)
    payload["capabilities"] = capabilities["features"]
    return payload
