from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field


router = APIRouter(prefix="/macro", tags=["macro"])

VALID_SERIES = {"DGS2", "DGS10", "DGS30", "T10Y2Y", "VIXCLS", "DTWEXBGS", "SP500"}
VALID_FOCUS = {"general", "qdii_impact", "fed_policy"}
VALID_DEPTH = {"brief", "detailed"}
CHINA_TZ = timezone(timedelta(hours=8))


def _derive_llm_audit_status(*, attempted: bool, has_output: bool) -> str:
    if not attempted:
        return "skipped"
    return "completed" if has_output else "failed"


class GenerateMacroAnalysisRequest(BaseModel):
    focus: str = Field(default="general", min_length=1, max_length=32)
    depth: str = Field(default="brief", min_length=1, max_length=32)


def _macro_reason(settings) -> str | None:
    if not settings.macro_monitor_enabled:
        return "config_disabled"
    if not settings.fred_api_key:
        return "missing_fred_api_key"
    return None


def _assert_macro_available(settings) -> None:
    reason = _macro_reason(settings)
    if reason is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)


def _build_rule_analysis(snapshot: dict[str, object], *, focus: str, depth: str) -> dict[str, object]:
    yields = snapshot.get("yields") if isinstance(snapshot, dict) else None
    y10 = yields.get("y10") if isinstance(yields, dict) else None
    y10_value = y10.get("value") if isinstance(y10, dict) else None
    y10_change = y10.get("changeD5Bp") if isinstance(y10, dict) else None
    direction = "neutral"
    if isinstance(y10_change, (int, float)):
        if y10_change > 5:
            direction = "negative"
        elif y10_change < -5:
            direction = "positive"
    level = "medium" if direction != "neutral" else "low"
    y10_text = f"{y10_value:.2f}%" if isinstance(y10_value, (int, float)) else "--"
    summary = f"美债10Y当前约 {y10_text}，宏观环境对 QDII 估值影响偏{ {'positive': '友好', 'negative': '承压', 'neutral': '中性'}[direction] }。"
    return {
        "summary": summary,
        "impactLevel": level,
        "impactDirection": direction,
        "reasoning": {
            "driver": "基于 FRED 收益率、VIX、美元指数与标普500快照生成的规则解读。",
            "qdiiImpact": "收益率上行通常增加美股成长资产估值压力；收益率回落则有助于缓解估值压力。",
            "timeHorizon": "短期",
        },
        "watch": {
            "recommendation": "watch",
            "specific": "关注10Y收益率是否继续上行、VIX是否同步抬升，以及标普500是否出现风险偏好回落。",
        },
        "risks": ["FRED为日度数据，非实时行情。", "规则解读不构成投资建议。"],
        "keyLevels": {"up": "10Y接近或突破4.8%", "down": "10Y回落至近期均值下方"},
        "confidence": 0.62,
        "focus": focus,
        "depth": depth,
        "engine": "rules",
    }


@router.get("/capabilities")
async def macro_capabilities(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    redis_store = request.app.state.redis_store
    reason = _macro_reason(settings)
    snapshot = await redis_store.get_macro_snapshot()
    analysis_service = getattr(request.app.state, "macro_analysis_service", None)
    llm_configured = bool(analysis_service and analysis_service.is_configured())
    return {
        "enabled": reason is None,
        "reason": reason,
        "dataSource": "fred",
        "hasSnapshot": snapshot is not None,
        "analysisEnabled": llm_configured,
        "analysisEngine": "llm" if llm_configured else "rules",
        "requiredConfig": {
            "macroMonitorEnabled": settings.macro_monitor_enabled,
            "fredApiKeyPresent": bool(settings.fred_api_key),
            "llmConfigPresent": llm_configured,
        },
    }


@router.get("/snapshot")
async def macro_snapshot(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    _assert_macro_available(settings)
    redis_store = request.app.state.redis_store
    snapshot = await redis_store.get_macro_snapshot()
    if snapshot is None:
        snapshot = await request.app.state.macro_query_service.fetch_snapshot()
    return {
        "enabled": True,
        "snapshot": snapshot,
        "collectorStatus": await redis_store.get_macro_collector_status(),
    }


@router.get("/history")
async def macro_history(
    request: Request,
    series: str = Query(default="DGS10"),
    limit: int = Query(default=90, ge=1, le=365),
) -> dict[str, object]:
    settings = request.app.state.settings
    _assert_macro_available(settings)
    normalized_series = series.strip().upper()
    if normalized_series not in VALID_SERIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid macro series")
    return {
        "series": normalized_series,
        "observations": await request.app.state.macro_query_service.fetch_history(normalized_series, limit=limit),
    }


@router.get("/analysis/latest")
async def latest_macro_analysis(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    _assert_macro_available(settings)
    cached = await request.app.state.redis_store.get_macro_analysis_latest()
    if cached is not None:
        return {"analysis": cached, "cacheHit": True}
    latest = await request.app.state.macro_query_service.fetch_latest_analysis()
    return {"analysis": latest, "cacheHit": False}


@router.post("/analysis/generate")
async def generate_macro_analysis(payload: GenerateMacroAnalysisRequest, request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    _assert_macro_available(settings)
    focus = payload.focus.strip().lower()
    depth = payload.depth.strip().lower()
    if focus not in VALID_FOCUS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid focus")
    if depth not in VALID_DEPTH:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid depth")

    redis_store = request.app.state.redis_store
    snapshot = await redis_store.get_macro_snapshot()
    if snapshot is None:
        snapshot = await request.app.state.macro_query_service.fetch_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="macro_snapshot_unavailable")

    analysis_result = request.app.state.macro_analysis_service.analyze(snapshot=snapshot, focus=focus, depth=depth)
    if analysis_result.analysis is None:
        analysis = _build_rule_analysis(snapshot, focus=focus, depth=depth)
        analysis["fallbackReason"] = analysis_result.skip_reason or "llm_unavailable"
        model_used = "rules"
        prompt_version = "rules-v1"
    else:
        analysis = analysis_result.analysis
        analysis["engine"] = "llm"
        model_used = analysis_result.model_used
        prompt_version = analysis_result.prompt_version
    invoked_at = datetime.now(UTC)
    saved = await request.app.state.macro_query_service.insert_analysis(
        trigger_type="manual",
        focus=focus,
        depth=depth,
        snapshot_date=str(snapshot.get("date")) if snapshot.get("date") else None,
        data_snapshot=snapshot,
        analysis=analysis,
        model_used=model_used,
        prompt_version=prompt_version,
        cache_key=f"manual:{focus}:{depth}:{snapshot.get('date') or datetime.now(UTC).date().isoformat()}",
    )
    await request.app.state.llm_audit_query_service.insert_audit_rows(
        [
            {
                "invoked_at": invoked_at,
                "audit_date": invoked_at.astimezone(CHINA_TZ).date(),
                "menu_module": "macro",
                "call_category": "macro_analysis",
                "status": _derive_llm_audit_status(attempted=analysis_result.attempted, has_output=analysis_result.analysis is not None),
                "model_used": analysis_result.model_used,
                "prompt_version": analysis_result.prompt_version,
                "latency_ms": analysis_result.latency_ms,
                "meta": {
                    "focus": focus,
                    "depth": depth,
                    "skipReason": analysis_result.skip_reason,
                    "snapshotDate": str(snapshot.get("date")) if snapshot.get("date") else None,
                    "attempts": analysis_result.attempts or [],
                },
            }
        ]
    )
    await redis_store.set_macro_analysis_latest(saved)
    return {"analysis": saved, "cacheHit": False}
