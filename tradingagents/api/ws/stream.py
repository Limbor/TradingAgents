"""WebSocket endpoints for real-time event streaming."""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from tradingagents.api.middleware.auth import verify_ws_origin, verify_ws_token
from tradingagents.core.orchestrator import merge_context_params
from tradingagents.skills.base import BaseSkill, SkillEvent

logger = logging.getLogger(__name__)
router = APIRouter()
TERMINAL_EVENT_TYPES = {"run_complete", "run_cancelled", "error"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

# Size guards for client-supplied chat payloads: context / intent_hint params
# are echoed into skill params and LLM prompts, so cap them before any work.
_MAX_MESSAGE_CHARS = 64_000
_MAX_CONTEXT_CHARS = 16_000


def _payload_oversized(message: dict) -> str | None:
    """Return a rejection reason when a chat message payload is too large."""
    try:
        if len(json.dumps(message, ensure_ascii=False)) > _MAX_MESSAGE_CHARS:
            return "message payload exceeds 64KB"
        context = message.get("context")
        if context is not None and len(json.dumps(context, ensure_ascii=False)) > _MAX_CONTEXT_CHARS:
            return "context exceeds 16KB"
        hint = message.get("intent_hint")
        if isinstance(hint, dict):
            params = hint.get("params")
            if params is not None and len(json.dumps(params, ensure_ascii=False)) > _MAX_CONTEXT_CHARS:
                return "intent_hint.params exceeds 16KB"
    except (TypeError, ValueError):
        return "payload is not JSON-serializable"
    return None


@dataclass
class _RouteResultStub:
    """Lightweight RouteResult-like for when ChatAgent decides to run a skill.

    Avoids importing orchestrator.RouteResult directly to keep ws_chat decoupled.
    """
    skill: BaseSkill
    params: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""


async def _reject_ws(websocket: WebSocket) -> None:
    """Reject a WebSocket that fails the auth check."""
    await websocket.accept()
    await websocket.send_json({"type": "error", "payload": {"message": "Unauthorized"}})
    await websocket.close(code=4401)


@router.websocket("/ws/run/{run_id}")
async def ws_run_stream(websocket: WebSocket, run_id: str):
    """Subscribe to real-time events for a specific run.

    Sends historical events first (for reconnection), then streams
    new events as they occur.
    """
    config = getattr(websocket.app.state, "config", {}) or {}
    if not verify_ws_token(websocket.query_params, config, websocket.headers) or not verify_ws_origin(
        websocket.headers, config
    ):
        await _reject_ws(websocket)
        return
    await websocket.accept(subprotocol=_accepted_subprotocol(websocket.headers))

    run_manager = websocket.app.state.run_manager
    db = getattr(websocket.app.state, "db", None)
    run = run_manager.get_run(run_id)

    if not run:
        # Run not in memory (server restarted) — try to replay persisted events
        # from the DB so the reconnecting client sees progress, not just a
        # "run not found" error. If there's no DB record either, fall through.
        if db is not None and hasattr(db, "list_run_events"):
            persisted = db.list_run_events(run_id)
            if persisted:
                for evt in persisted:
                    await websocket.send_json({
                        "type": evt.get("event_type") or "event",
                        "run_id": run_id,
                        "timestamp": evt.get("created_at") or _now(),
                        "payload": evt.get("payload") or {},
                    })
                # Send a synthetic terminal event so the client knows it's done.
                await websocket.send_json(_terminal_event_for(run_id, db))
                await websocket.close(code=1000)
                return
        await websocket.send_json({
            "type": "error",
            "payload": {"message": "Run not found"},
        })
        await websocket.close(code=4004)
        return

    queue = run_manager.subscribe(run_id)
    sent_event_ids: set[int] = set()

    try:
        for event in run.events:
            sent_event_ids.add(id(event))
            await websocket.send_json(_serialize_event(run_id, event))
            if event.event_type in TERMINAL_EVENT_TYPES:
                await websocket.close(code=1000)
                return

        if run.status.value in TERMINAL_STATUSES:
            await websocket.send_json(_terminal_event(run_id, run))
            await websocket.close(code=1000)
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                if id(event) in sent_event_ids:
                    continue
                sent_event_ids.add(id(event))
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
    """Natural language chat endpoint that routes messages to skills.

    The run event stream is consumed in a background task so the main
    ``receive_json`` loop stays responsive: the client can send
    ``{"action":"cancel","run_id":...}`` (or just ``{"action":"cancel"}`` to
    cancel the active run) while a run is in progress, then immediately submit
    a new request. A send-lock serializes all socket writes so the background
    consumer and the main loop never interleave partial frames.
    """
    config = getattr(websocket.app.state, "config", {}) or {}
    if not verify_ws_token(websocket.query_params, config, websocket.headers) or not verify_ws_origin(
        websocket.headers, config
    ):
        await _reject_ws(websocket)
        return
    await websocket.accept(subprotocol=_accepted_subprotocol(websocket.headers))
    orchestrator = websocket.app.state.orchestrator
    run_manager = websocket.app.state.run_manager
    config = websocket.app.state.config

    # Session ID for conversation context (LLM Router multi-turn support)
    session_id = str(uuid.uuid4())
    active_run_id: str | None = None
    active_consumer: asyncio.Task | None = None
    send_lock = asyncio.Lock()

    async def _send(payload: dict) -> None:
        """Serialize socket writes so the consumer + main loop can't interleave."""
        async with send_lock:
            with contextlib.suppress(Exception):
                await websocket.send_json(payload)

    async def _consume_run_events(run, queue) -> None:
        """Stream a run's events to the client as a background task."""
        sent_event_ids: set[int] = set()
        try:
            for event in run.events:
                sent_event_ids.add(id(event))
                await _send(_serialize_event(run.id, event))
                if event.event_type in TERMINAL_EVENT_TYPES:
                    return
            if run.status.value in TERMINAL_STATUSES:
                await _send(_terminal_event(run.id, run))
                return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    await _send({
                        "type": "heartbeat",
                        "run_id": run.id,
                        "timestamp": _now(),
                        "payload": {},
                    })
                    continue
                if id(event) in sent_event_ids:
                    continue
                sent_event_ids.add(id(event))
                await _send(_serialize_event(run.id, event))
                if event.event_type in TERMINAL_EVENT_TYPES:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # Swallow unexpected errors so a flaky consumer never kills the socket.
            pass
        finally:
            run_manager.unsubscribe(run.id, queue)

    async def _resolve_route(user_text: str, msg_ctx: dict | None, hint: Any):
        """Resolve one chat message to a skill route.

        Order: intent_hint fast path (deterministic, 0 LLM calls) → ChatAgent
        (LLM tool-use) → orchestrator regex fallback. Direct replies
        (chat_answer / tool_answer / clarify / …) are sent from here and
        ``None`` is returned; a non-None return always carries a skill.
        """
        # Intent hint fast path: page jump buttons name the target skill
        # directly, so a click never depends on probabilistic LLM routing.
        if isinstance(hint, dict):
            hint_route = orchestrator.route_hint(hint, context=msg_ctx)
            if hint_route.skill is not None:
                return hint_route
            # Degrade to normal text routing so the button always gets a reply.
            logger.warning(
                "intent_hint rejected, degrading to text routing: %s",
                hint_route.reason,
            )

        # ChatAgent (LLM tool-use) is the primary router: it both answers
        # free-form questions and extracts skill params (limit, industries,
        # ticker, ...) that the regex router would silently drop. The
        # deterministic orchestrator regex only kicks in when the LLM path
        # is unavailable or degraded (init failure / timeout / call error).
        chat_response = None
        degraded_content = ""
        chat_agent = getattr(websocket.app.state, "chat_agent", None)
        if chat_agent is not None:
            try:
                chat_response = await chat_agent.handle(
                    user_text,
                    session_id=session_id,
                    context=msg_ctx,
                )
            except Exception:
                logger.exception("ChatAgent.handle failed")
                chat_response = None
        if chat_response is not None and getattr(chat_response, "degraded", False):
            degraded_content = chat_response.content or ""
            chat_response = None

        if chat_response is not None:
            if chat_response.intent == "chat_answer":
                await _send({
                    "type": "chat_answer",
                    "run_id": "",
                    "timestamp": _now(),
                    "payload": {
                        "content": chat_response.content,
                        "citations": chat_response.citations,
                    },
                })
                return None
            elif chat_response.intent == "tool_answer":
                await _send({
                    "type": "tool_answer",
                    "run_id": "",
                    "timestamp": _now(),
                    "payload": {
                        "content": chat_response.content,
                        "tool": chat_response.tool_name,
                        "args": chat_response.tool_args,
                        "result": chat_response.tool_result,
                        "display": chat_response.tool_display,
                        "citations": chat_response.citations,
                    },
                })
                return None
            elif chat_response.intent == "clarify":
                await _send({
                    "type": "clarify",
                    "run_id": "",
                    "timestamp": _now(),
                    "payload": {
                        "question": chat_response.clarify_question or chat_response.content or "请问您需要什么帮助？",
                        "options": chat_response.clarify_options,
                    },
                })
                return None
            elif chat_response.intent == "skill_run":
                # ChatAgent decided to run a skill — create a run. Schema-driven
                # context merge so a selection/holding context carried by the
                # message (e.g. user clicked "分析" on a candidate) is not lost
                # when the request goes through ChatAgent instead of regex.
                skill = websocket.app.state.registry.get(chat_response.skill_id)
                if skill is None:
                    await _send({
                        "type": "chat_answer",
                        "run_id": "",
                        "timestamp": _now(),
                        "payload": {"content": f"抱歉，找不到 '{chat_response.skill_id}' 这个功能。"},
                    })
                    return None
                skill_params = merge_context_params(
                    skill, dict(chat_response.skill_params or {}), msg_ctx
                )
                return _RouteResultStub(skill, skill_params, 0.85, "ChatAgent skill_run")
            else:
                await _send({
                    "type": "chat_answer",
                    "run_id": "",
                    "timestamp": _now(),
                    "payload": {"content": chat_response.content or "抱歉，我不太理解您的意思。"},
                })
                return None

        # ChatAgent unavailable or degraded — deterministic regex fallback.
        route = await orchestrator.route(
            user_text,
            session_id=session_id,
            context=msg_ctx,
        )
        if route.skill is None:
            await _send({
                "type": "chat_reply",
                "run_id": "",
                "timestamp": _now(),
                "payload": {
                    "content": degraded_content
                    or "AI 路由暂时不可用，无法理解该请求，请稍后重试或换一种说法。",
                    "reason": route.reason,
                },
            })
            return None
        return route

    try:
        while True:
            message = await websocket.receive_json()

            # Cancel the active (or specified) run without starting a new one.
            if str(message.get("action", "")).lower() == "cancel":
                target = str(message.get("run_id") or active_run_id or "")
                if target:
                    cancelled = await run_manager.cancel_run(target)
                    await _send({
                        "type": "run_cancellation_ack",
                        "run_id": target,
                        "timestamp": _now(),
                        "payload": {"cancelled": cancelled},
                    })
                continue

            user_text = str(message.get("message", "")).strip()
            # Allow client to specify session_id for reconnection
            if message.get("session_id"):
                session_id = str(message["session_id"])
            if not user_text:
                await _send({
                    "type": "chat_reply",
                    "run_id": "",
                    "timestamp": _now(),
                    "payload": {"content": "Please enter a request."},
                })
                continue

            msg_ctx = message.get("context") if isinstance(message.get("context"), dict) else None

            # Reject oversized payloads before any routing / LLM work: context
            # and intent_hint params flow into skill params and LLM prompts.
            oversize_reason = _payload_oversized(message)
            if oversize_reason:
                await _send({
                    "type": "chat_reply",
                    "run_id": "",
                    "timestamp": _now(),
                    "payload": {
                        "content": "请求携带的上下文过大，已拒绝处理，请减少携带的数据后重试。",
                        "reason": oversize_reason,
                    },
                })
                continue

            route = await _resolve_route(user_text, msg_ctx, message.get("intent_hint"))
            if route is None or route.skill is None:
                # A direct reply (chat/tool/clarify/rejection) was already sent.
                continue

            run = await run_manager.create_run(route.skill, route.params, config)
            queue = run_manager.subscribe(run.id)
            await _send({
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
            })

            # Cancel any still-running prior consumer before starting a new one.
            if active_consumer is not None and not active_consumer.done():
                active_consumer.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await active_consumer

            active_run_id = run.id
            active_consumer = asyncio.create_task(_consume_run_events(run, queue))
    except WebSocketDisconnect:
        pass
    finally:
        if active_consumer is not None and not active_consumer.done():
            active_consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await active_consumer


def _serialize_event(run_id: str, event: SkillEvent) -> dict:
    """Serialize a SkillEvent for WebSocket transmission."""
    return {
        "type": event.event_type,
        "run_id": run_id,
        "timestamp": _now(),
        "payload": event.data,
    }


def _accepted_subprotocol(headers) -> str | None:
    """Echo the non-secret application protocol requested by browser clients."""
    offered = ((headers.get("sec-websocket-protocol") if headers else "") or "").split(",")
    return "tradingagents" if "tradingagents" in {item.strip() for item in offered} else None


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


def _terminal_event_for(run_id: str, db) -> dict:
    """Build a synthetic terminal event from a persisted run record (used when
    the run is no longer in memory after a server restart)."""
    status = "failed"
    error = None
    try:
        record = db.get_run(run_id) if hasattr(db, "get_run") else None
        if record:
            status = str(record.get("status") or "failed")
            error = record.get("error")
    except Exception:
        pass
    if status == "completed":
        event = SkillEvent(event_type="run_complete", data={"status": status, "result": {}})
    elif status == "cancelled":
        event = SkillEvent(event_type="run_cancelled", data={"status": status})
    else:
        event = SkillEvent(event_type="error", data={"message": error or "Run failed"})
    return _serialize_event(run_id, event)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
