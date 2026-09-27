"""Same-origin bridge to StockManager strategy paper sessions."""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tradingagents.core.stockmanager_paper import PaperServiceError, paper_request

router = APIRouter(prefix="/paper")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


def _id(value: str) -> str:
    if not _ID.fullmatch(value) or ".." in value:
        raise HTTPException(400, "无效的会话或任务 ID")
    return value


async def _call(request: Request, method: str, path: str, payload: dict | None = None) -> dict:
    try:
        return await paper_request(request.app.state.config, method, path, payload)
    except PaperServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


class CreatePaperSession(BaseModel):
    strategy: str = Field(default="", max_length=160)
    config: str = Field(default="", max_length=160)
    allocator_config_path: str | None = Field(default=None, max_length=500)
    initial_cash: float = Field(default=1_000_000, gt=0)
    start_date: date | None = None
    universe_size: int | None = Field(default=None, gt=0)
    warmup_days: int = Field(default=760, ge=0)
    suffix: str = Field(default="default", max_length=100)


class AdvancePaper(BaseModel):
    target_date: date
    expected_state_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class AdvancePaperReview(BaseModel):
    job_id: str = Field(min_length=1, max_length=160)
    observed_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool


@router.get("/strategies")
async def strategies(request: Request):
    return (await _call(request, "GET", "/api/v2/strategies")).get("items", [])


@router.get("/configs")
async def configs(request: Request):
    return (await _call(request, "GET", "/api/v2/configs")).get("items", [])


@router.get("/allocator-configs")
async def allocator_configs(request: Request):
    return (await _call(request, "GET", "/api/v2/allocator-configs")).get("items", [])


@router.get("/sessions")
async def sessions(request: Request):
    return (await _call(request, "GET", "/api/v2/sessions?mode=paper")).get("items", [])


@router.post("/sessions")
async def create_session(request: Request, body: CreatePaperSession):
    if not body.allocator_config_path and (not body.start_date or not body.strategy):
        raise HTTPException(422, "单策略模拟盘需要策略和起始日期")
    if body.allocator_config_path:
        available = (await _call(request, "GET", "/api/v2/allocator-configs")).get("items", [])
        if body.allocator_config_path not in {item.get("path") for item in available}:
            raise HTTPException(422, "组合配置不在 StockManager 可用列表中")
    payload = body.model_dump(mode="json", exclude_none=True)
    return await _call(request, "POST", "/api/v2/sessions", {"mode": "paper", **payload})


@router.get("/jobs/{job_id}")
async def job(request: Request, job_id: str):
    return await _call(request, "GET", f"/api/jobs/{_id(job_id)}")


@router.get("/sessions/{session_id}/status")
async def status(request: Request, session_id: str):
    return (await _call(request, "GET", f"/api/v2/paper/{_id(session_id)}/status")).get("data")


@router.get("/sessions/{session_id}/equity")
async def equity(request: Request, session_id: str):
    return (await _call(request, "GET", f"/api/v2/paper/{_id(session_id)}/equity_curve")).get("data")


@router.get("/sessions/{session_id}/trades")
async def trades(request: Request, session_id: str):
    return (await _call(request, "GET", f"/api/v2/paper/{_id(session_id)}/trades?limit=500")).get("items", [])


@router.get("/sessions/{session_id}/next-plan")
async def next_plan(request: Request, session_id: str):
    return (await _call(request, "GET", f"/api/v2/paper/{_id(session_id)}/next_plan")).get("data")


@router.post("/sessions/{session_id}/advance")
async def advance(request: Request, session_id: str, body: AdvancePaper):
    return await _call(
        request, "POST", f"/api/v2/paper/{_id(session_id)}/advance",
        {"target_date": body.target_date.isoformat(),
         **({"expected_state_fingerprint": body.expected_state_fingerprint}
            if body.expected_state_fingerprint is not None else {})},
    )


@router.post("/sessions/{session_id}/advance-review")
async def advance_review(request: Request, session_id: str, body: AdvancePaperReview):
    if not body.confirmed:
        raise HTTPException(422, "必须确认已核对账本")
    return await _call(
        request, "POST", f"/api/v2/paper/{_id(session_id)}/advance_review",
        {"job_id": _id(body.job_id), "observed_state_fingerprint": body.observed_state_fingerprint,
         "confirmed": True},
    )
