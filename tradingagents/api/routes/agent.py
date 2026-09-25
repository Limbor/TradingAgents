"""Persistent trading Agent conversations and read-only tasks."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/agent")


class NewConversation(BaseModel):
    title: str = Field(default="新对话", max_length=120)
    paper_session_id: str | None = Field(default=None, max_length=160)


class NewTask(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    intent_hint: dict | None = None


@router.get("/conversations")
def list_conversations(request: Request):
    return request.app.state.agent_store.list_conversations()


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, body: NewConversation):
    try:
        return request.app.state.agent_store.create_conversation(
            body.title.strip() or "新对话", body.paper_session_id
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/conversations/{conversation_id}")
def get_conversation(request: Request, conversation_id: str):
    detail = request.app.state.agent_store.conversation_detail(conversation_id)
    if detail is None:
        raise HTTPException(404, "对话不存在")
    return detail


@router.post("/conversations/{conversation_id}/tasks", status_code=202)
async def create_task(request: Request, conversation_id: str, body: NewTask):
    try:
        if body.intent_hint and len(json.dumps(body.intent_hint, ensure_ascii=False)) > 4096:
            raise ValueError("指定技能参数过大")
        return request.app.state.agent_harness.submit(conversation_id, body.message,
                                                      body.intent_hint)
    except KeyError as exc:
        raise HTTPException(404, "对话不存在") from exc
    except ValueError as exc:
        raise HTTPException(409 if "运行中" in str(exc) else 422, str(exc)) from exc


@router.get("/tasks/{task_id}")
def get_task(request: Request, task_id: str):
    task = request.app.state.agent_store.get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return {**task, "events": request.app.state.agent_store.list_events(task_id),
            "evidence": request.app.state.agent_store.list_evidence(task_id),
            "proposal": request.app.state.agent_store.proposal_for_task(task_id)}


@router.get("/tasks/{task_id}/events")
def list_events(request: Request, task_id: str, after_seq: int = 0):
    if request.app.state.agent_store.get_task(task_id) is None:
        raise HTTPException(404, "任务不存在")
    return request.app.state.agent_store.list_events(task_id, after_seq)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str):
    if request.app.state.agent_store.get_task(task_id) is None:
        raise HTTPException(404, "任务不存在")
    return {"cancelled": await request.app.state.agent_harness.cancel(task_id)}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(request: Request, proposal_id: str):
    try:
        return request.app.state.agent_harness.approve(proposal_id)
    except KeyError as exc:
        raise HTTPException(404, "提案不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(request: Request, proposal_id: str):
    try:
        return request.app.state.agent_harness.reject(proposal_id)
    except KeyError as exc:
        raise HTTPException(404, "提案不存在") from exc
