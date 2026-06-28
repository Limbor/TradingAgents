"""Run management endpoints — create, list, get, cancel."""

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
    )


@router.get("/runs", response_model=list[RunResponse])
async def list_runs(request: Request, limit: int = 50):
    """List recent runs."""
    run_manager = request.app.state.run_manager
    runs = run_manager.list_runs(limit=limit)
    return [
        RunResponse(
            id=r.id,
            skill_id=r.skill_id,
            status=r.status.value,
            created_at=r.created_at.isoformat(),
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            error=r.error,
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
    )


@router.delete("/runs/{run_id}")
async def cancel_run(request: Request, run_id: str):
    """Cancel a running task."""
    run_manager = request.app.state.run_manager
    success = await run_manager.cancel_run(run_id)
    if not success:
        raise HTTPException(status_code=404, detail="Run not found or already finished")
    return {"status": "cancelled"}
