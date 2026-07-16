"""Structured portfolio risk-event endpoints."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()


class RiskEventItem(BaseModel):
    id: str
    symbol: str
    name: str | None = None
    level: str
    event_type: str
    title: str
    source: str = ""
    event_date: str | None = None
    status: str
    first_seen_at: str
    last_seen_at: str
    resolved_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RiskEventStatusUpdate(BaseModel):
    status: Literal["open", "acknowledged", "monitoring", "resolved"]


@router.get("/risk-events", response_model=list[RiskEventItem])
async def list_risk_events(
    request: Request,
    status: str | None = "open",
    symbol: str | None = None,
    level: str | None = None,
    limit: int = 100,
):
    return request.app.state.db.list_risk_events(
        status=status,
        symbol=symbol,
        level=level,
        limit=limit,
    )


@router.patch("/risk-events/{event_id}", response_model=RiskEventItem)
async def update_risk_event(
    event_id: str, body: RiskEventStatusUpdate, request: Request
):
    item = request.app.state.db.update_risk_event_status(event_id, body.status)
    if item is None:
        raise HTTPException(status_code=404, detail="risk event not found")
    return item
