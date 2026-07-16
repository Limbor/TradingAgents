"""Run management endpoints — create, list, get, cancel, timeline."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class CreateRunRequest(BaseModel):
    skill_id: str
    params: dict = {}


class RunResponse(BaseModel):
    id: str
    skill_id: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    params: dict | None = None


@router.post("/runs", response_model=RunResponse)
async def create_run(request: Request, body: CreateRunRequest):
    """Create and start a new Skill run."""
    registry = request.app.state.registry
    run_manager = request.app.state.run_manager
    config = request.app.state.config

    skill = registry.get(body.skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{body.skill_id}' not found")

    run = await run_manager.create_run(skill, body.params, config)
    return RunResponse(
        id=run.id,
        skill_id=run.skill_id,
        status=run.status.value,
        created_at=run.created_at.isoformat(),
        params=run.params,
    )


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(request: Request, limit: int = 50, offset: int = 0):
    """List recent runs with pagination."""
    run_manager = request.app.state.run_manager
    runs = run_manager.list_runs(limit=limit, offset=offset)
    return [
        RunResponse(
            id=r.id,
            skill_id=r.skill_id,
            status=r.status.value,
            created_at=r.created_at.isoformat(),
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            error=r.error,
            params=r.params,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(request: Request, run_id: str):
    """Get details for a specific run."""
    run_manager = request.app.state.run_manager
    run = run_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunResponse(
        id=run.id,
        skill_id=run.skill_id,
        status=run.status.value,
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        error=run.error,
        params=run.params,
    )


@router.delete("/runs/{run_id}")
async def cancel_run(request: Request, run_id: str):
    """Cancel a running task."""
    run_manager = request.app.state.run_manager
    success = await run_manager.cancel_run(run_id)
    if not success:
        raise HTTPException(status_code=404, detail="Run not found or already finished")
    return {"status": "cancelled"}


class TimelineEntry(BaseModel):
    id: str
    skill_id: str
    status: str
    created_at: str
    completed_at: str | None = None
    params: dict | None = None
    ticker: str | None = None
    skill_label: str | None = None


SKILL_LABELS = {
    "stock_analysis": "股票分析",
    "daily_pipeline": "每日选股",
    "market_scanner": "市场扫描",
    "risk_monitor": "风险监控",
    "portfolio_management": "持仓管理",
    "daily_review": "收盘复盘",
}


@router.get("/timeline", response_model=list[TimelineEntry])
async def get_timeline(request: Request, since: str = "today", limit: int = 30):
    """Get timeline events for dashboard. Supports since=today or ISO date."""
    run_manager = request.app.state.run_manager
    runs = run_manager.list_runs(limit=100)

    # Determine cutoff time
    if since == "today":
        now = datetime.now(timezone.utc)
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        try:
            cutoff = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        except ValueError:
            cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    entries = []
    for r in runs:
        if r.created_at >= cutoff:
            ticker = None
            if r.params:
                ticker = r.params.get("ticker") or r.params.get("symbol")
                if ticker:
                    ticker = str(ticker)
            entries.append(TimelineEntry(
                id=r.id,
                skill_id=r.skill_id,
                status=r.status.value,
                created_at=r.created_at.isoformat(),
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
                params=r.params,
                ticker=ticker,
                skill_label=SKILL_LABELS.get(r.skill_id, r.skill_id),
            ))
        if len(entries) >= limit:
            break

    return entries
