from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.fund_portfolio_risk_analysis_service import build_rule_fund_portfolio_analysis


router = APIRouter(prefix="/funds", tags=["funds"])
_FUND_CODE_RE = re.compile(r"^\d{6}$")
VALID_PORTFOLIO_ANALYSIS_FOCUS = {"general", "overlap", "concentration"}
VALID_PORTFOLIO_ANALYSIS_DEPTH = {"brief", "detailed"}
CHINA_TZ = timezone(timedelta(hours=8))


class ActivateFundRequest(BaseModel):
    fundCode: str = Field(min_length=1, max_length=16)
    autoLinkStocks: bool = True


class GenerateFundPortfolioAnalysisRequest(BaseModel):
    focus: str = Field(default="general", min_length=1, max_length=32)
    depth: str = Field(default="brief", min_length=1, max_length=32)


def _derive_llm_audit_status(*, attempted: bool, has_output: bool) -> str:
    if not attempted:
        return "skipped"
    return "completed" if has_output else "failed"


def _normalize_fund_code_or_422(fund_code: str | None) -> str:
    normalized = (fund_code or "").strip()
    if not _FUND_CODE_RE.match(normalized):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="fundCode must be a 6 digit fund code")
    return normalized


@router.get("/active")
async def active_funds(request: Request) -> dict[str, list[str]]:
    return {"funds": await request.app.state.redis_store.get_active_funds()}


@router.get("/snapshots")
async def active_fund_snapshots(request: Request) -> dict[str, dict[str, object]]:
    redis_store = request.app.state.redis_store
    query_service = request.app.state.fund_query_service
    fund_codes = await redis_store.get_active_funds()
    snapshots = await redis_store.get_fund_snapshots(fund_codes)
    query_snapshots = await query_service.fetch_active_fund_snapshots(fund_codes)
    for fund_code, query_snapshot in query_snapshots.items():
        if fund_code in snapshots:
            snapshots[fund_code] = {
                **snapshots[fund_code],
                **query_snapshot,
            }
        else:
            snapshots[fund_code] = query_snapshot
    return {"snapshots": snapshots}


@router.get("/portfolio")
async def fund_portfolio_view(request: Request) -> dict[str, object]:
    fund_codes = await request.app.state.redis_store.get_active_funds()
    return await request.app.state.fund_query_service.fetch_active_fund_portfolio_view(fund_codes)


@router.post("/portfolio/analysis")
async def generate_fund_portfolio_analysis(
    payload: GenerateFundPortfolioAnalysisRequest,
    request: Request,
) -> dict[str, object]:
    focus = payload.focus.strip().lower()
    depth = payload.depth.strip().lower()
    if focus not in VALID_PORTFOLIO_ANALYSIS_FOCUS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid focus")
    if depth not in VALID_PORTFOLIO_ANALYSIS_DEPTH:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid depth")

    fund_codes = await request.app.state.redis_store.get_active_funds()
    portfolio_view = await request.app.state.fund_query_service.fetch_active_fund_portfolio_view(fund_codes)
    analysis_result = request.app.state.fund_portfolio_risk_analysis_service.analyze(
        portfolio_view=portfolio_view,
        focus=focus,
        depth=depth,
    )
    if analysis_result.analysis is None:
        analysis = build_rule_fund_portfolio_analysis(portfolio_view, focus=focus, depth=depth)
        analysis["engine"] = "rules"
        analysis["fallbackReason"] = analysis_result.skip_reason or "llm_unavailable"
        model_used = "rules"
        prompt_version = "fund-portfolio-rules-v1"
    else:
        analysis = analysis_result.analysis
        analysis["engine"] = "llm"
        model_used = analysis_result.model_used
        prompt_version = analysis_result.prompt_version
    invoked_at = datetime.now(UTC)
    await request.app.state.llm_audit_query_service.insert_audit_rows(
        [
            {
                "invoked_at": invoked_at,
                "audit_date": invoked_at.astimezone(CHINA_TZ).date(),
                "menu_module": "funds",
                "call_category": "fund_portfolio_risk_analysis",
                "status": _derive_llm_audit_status(
                    attempted=analysis_result.attempted,
                    has_output=analysis_result.analysis is not None,
                ),
                "model_used": analysis_result.model_used,
                "prompt_version": analysis_result.prompt_version,
                "latency_ms": analysis_result.latency_ms,
                "meta": {
                    "focus": focus,
                    "depth": depth,
                    "portfolioStatus": portfolio_view.get("status"),
                    "activeFundCount": ((portfolio_view.get("summary") or {}).get("activeFundCount") if isinstance(portfolio_view.get("summary"), dict) else None),
                    "fallbackReason": analysis_result.skip_reason,
                },
            }
        ]
    )
    return {
        "analysis": analysis,
        "engine": analysis.get("engine"),
        "cacheHit": False,
        "modelUsed": model_used,
        "promptVersion": prompt_version,
    }


@router.get("/{fund_code}/detail")
async def fund_detail(fund_code: str, request: Request) -> dict[str, object]:
    normalized_fund_code = _normalize_fund_code_or_422(fund_code)
    return {
        "fundCode": normalized_fund_code,
        **await request.app.state.fund_query_service.fetch_fund_detail(normalized_fund_code),
    }


@router.post("/activate")
async def activate_fund(payload: ActivateFundRequest, request: Request) -> JSONResponse:
    fund_code = _normalize_fund_code_or_422(payload.fundCode)
    redis_store = request.app.state.redis_store
    if await redis_store.is_fund_active(fund_code):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "already_active",
                "fundCode": fund_code,
                "message": f"基金 {fund_code} 已在监控列表中",
            },
        )

    try:
        lookup_result = request.app.state.fund_lookup_service.lookup(fund_code)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"基金校验失败：{exc}") from exc

    if not lookup_result.is_valid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"基金代码 {fund_code} 不存在")

    await request.app.state.fund_query_service.upsert_fund_profile(
        fund_code=fund_code,
        payload={
            "fundCode": fund_code,
            "fundName": lookup_result.fund_name,
            "source": "eastmoney-fundgz",
            "rawPayload": lookup_result.raw_payload,
        },
    )
    if lookup_result.nav is not None:
        snapshot = {
            "fundCode": fund_code,
            "fundName": lookup_result.fund_name,
            "nav": lookup_result.nav,
            "dailyReturn": lookup_result.daily_return,
            "navDate": lookup_result.nav_date,
            "estimatedIntradayReturn": lookup_result.daily_return,
            "source": "eastmoney-fundgz",
        }
        await request.app.state.fund_query_service.upsert_fund_snapshot(fund_code=fund_code, payload=snapshot)
        await redis_store.set_fund_snapshot(fund_code, snapshot)

    await redis_store.activate_fund(fund_code, payload.autoLinkStocks)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "fundCode": fund_code,
            "fundName": lookup_result.fund_name,
            "autoLinkStocks": payload.autoLinkStocks,
            "message": f"已将 {fund_code} {lookup_result.fund_name or ''} 加入基金监控队列".strip(),
        },
    )


@router.delete("/{fund_code}", status_code=status.HTTP_202_ACCEPTED)
async def deactivate_fund(fund_code: str, request: Request) -> dict[str, str]:
    normalized_fund_code = _normalize_fund_code_or_422(fund_code)
    await request.app.state.redis_store.deactivate_fund(normalized_fund_code)
    return {
        "status": "accepted",
        "fundCode": normalized_fund_code,
        "message": f"collector fund deactivation queued for {normalized_fund_code}",
    }
