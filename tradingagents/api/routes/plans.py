"""Trade plan endpoints — create / list / update / delete monitored plans.

A plan is the structured, monitorable output of a selection or single-stock
analysis (entry zone, stop loss, targets, position sizing, conditions). The
user adopts a plan (status ``active``) so the daily close job can evaluate its
conditions and flag triggers.
"""

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class PlanInput(BaseModel):
    symbol: str
    name: str | None = None
    entry_zone: list[float] | None = None
    stop_loss: float | None = None
    targets: list[float] | None = None
    position_pct: float | None = None
    conditions: list[dict] | None = None
    rating: str | None = None
    status: str = "draft"  # draft / active / triggered / closed
    source: str = "analysis"  # selection / analysis
    artifact_id: str = ""
    reflection_case_id: str = ""


class PlanUpdate(BaseModel):
    status: str | None = None
    reflection_case_id: str | None = None


@router.post("/plans")
async def create_plan(request: Request, body: PlanInput):
    db = request.app.state.db
    plan_id = f"plan:{uuid.uuid4()}"
    db.save_plan(
        plan_id,
        symbol=body.symbol,
        name=body.name,
        entry_zone=body.entry_zone,
        stop_loss=body.stop_loss,
        targets=body.targets,
        position_pct=body.position_pct,
        conditions=body.conditions,
        rating=body.rating,
        status=body.status,
        source=body.source,
        artifact_id=body.artifact_id,
        reflection_case_id=body.reflection_case_id,
    )
    return db.get_plan(plan_id)


@router.get("/plans")
async def list_plans(
    request: Request,
    status: str | None = None,
    symbol: str | None = None,
    source: str | None = None,
    limit: int = 50,
):
    return request.app.state.db.list_plans(limit=limit, status=status, symbol=symbol, source=source)


@router.get("/plans/{plan_id}")
async def get_plan(request: Request, plan_id: str):
    plan = request.app.state.db.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.patch("/plans/{plan_id}")
async def update_plan(request: Request, plan_id: str, body: PlanUpdate):
    db = request.app.state.db
    if db.get_plan(plan_id) is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    db.update_plan(plan_id, status=body.status, reflection_case_id=body.reflection_case_id)
    return db.get_plan(plan_id)


@router.delete("/plans/{plan_id}")
async def delete_plan(request: Request, plan_id: str):
    deleted = request.app.state.db.delete_plan(plan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"deleted": deleted}
