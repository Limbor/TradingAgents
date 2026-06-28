"""WebSocket endpoints for real-time event streaming."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tradingagents.skills.base import SkillEvent

router = APIRouter()
TERMINAL_EVENT_TYPES = {"run_complete", "run_cancelled", "error"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@router.websocket("/ws/run/{run_id}")
async def ws_run_stream(websocket: WebSocket, run_id: str):
    """Subscribe to real-time events for a specific run.

    Sends historical events first (for reconnection), then streams
    new events as they occur.
    """
    await websocket.accept()

    run_manager = websocket.app.state.run_manager
    run = run_manager.get_run(run_id)

    if not run:
        await websocket.send_json({
            "type": "error",
            "payload": {"message": "Run not found"},
        })
        await websocket.close(code=4004)
        return

    # Send historical events (for reconnection)
    for event in run.events:
        await websocket.send_json(_serialize_event(run_id, event))

    if run.status.value in TERMINAL_STATUSES:
        if not run.events or run.events[-1].event_type not in TERMINAL_EVENT_TYPES:
            await websocket.send_json(_terminal_event(run_id, run))
        await websocket.close(code=1000)
        return

    # Subscribe to new events
    queue = run_manager.subscribe(run_id)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(_serialize_event(run_id, event))
                if event.event_type in TERMINAL_EVENT_TYPES:
                    await websocket.close(code=1000)
                    break
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "heartbeat",
                    "run_id": run_id,
                    "timestamp": _now(),
                    "payload": {},
                })
    except WebSocketDisconnect:
        pass
    finally:
        run_manager.unsubscribe(run_id, queue)


@router.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    """Natural language chat endpoint that routes messages to skills."""
    await websocket.accept()
    orchestrator = websocket.app.state.orchestrator
    run_manager = websocket.app.state.run_manager
    config = websocket.app.state.config

    try:
        while True:
            message = await websocket.receive_json()
            user_text = str(message.get("message", "")).strip()
            if not user_text:
                await websocket.send_json(
                    {
                        "type": "chat_reply",
                        "run_id": "",
                        "timestamp": _now(),
                        "payload": {"content": "Please enter a request."},
                    }
                )
                continue

            route = await orchestrator.route(user_text)
            if route.skill is None:
                await websocket.send_json(
                    {
                        "type": "chat_reply",
                        "run_id": "",
                        "timestamp": _now(),
                        "payload": {
                            "content": "I could not map that request to a registered skill.",
                            "reason": route.reason,
                        },
                    }
                )
                continue

            run = await run_manager.create_run(route.skill, route.params, config)
            queue = run_manager.subscribe(run.id)
            await websocket.send_json(
                {
                    "type": "chat_reply",
                    "run_id": run.id,
                    "timestamp": _now(),
                    "payload": {
                        "content": f"Routing to {route.skill.metadata.name}.",
                        "skill_triggered": route.skill.metadata.id,
                        "params": route.params,
                        "confidence": route.confidence,
                        "reason": route.reason,
                        "run_id": run.id,
                    },
                }
            )

            try:
                for event in run.events:
                    await websocket.send_json(_serialize_event(run.id, event))
                    if event.event_type in TERMINAL_EVENT_TYPES:
                        break
                if run.status.value in TERMINAL_STATUSES:
                    if not run.events or run.events[-1].event_type not in TERMINAL_EVENT_TYPES:
                        await websocket.send_json(_terminal_event(run.id, run))
                    continue
                while True:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    await websocket.send_json(_serialize_event(run.id, event))
                    if event.event_type in TERMINAL_EVENT_TYPES:
                        break
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "run_id": run.id,
                        "timestamp": _now(),
                        "payload": {},
                    }
                )
            finally:
                run_manager.unsubscribe(run.id, queue)
    except WebSocketDisconnect:
        pass


def _serialize_event(run_id: str, event: SkillEvent) -> dict:
    """Serialize a SkillEvent for WebSocket transmission."""
    return {
        "type": event.event_type,
        "run_id": run_id,
        "timestamp": _now(),
        "payload": event.data,
    }


def _terminal_event(run_id: str, run) -> dict:
    """Build a synthetic terminal event for already-finished historical runs."""
    if run.status.value == "completed":
        event = SkillEvent(
            event_type="run_complete",
            data={"status": run.status.value, "result": run.result or {}},
        )
    elif run.status.value == "cancelled":
        event = SkillEvent(
            event_type="run_cancelled",
            data={"status": run.status.value},
        )
    else:
        event = SkillEvent(
            event_type="error",
            data={"message": run.error or "Run failed"},
        )
    return _serialize_event(run_id, event)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
