"""Run lifecycle management.

A Run represents a single execution of a Skill. The RunManager creates,
tracks, and cancels runs, broadcasting events to subscribers (WebSocket
endpoints) via per-run asyncio Queues.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from tradingagents.core.agent_runtime import (
    AgentContext,
    AgentSpec,
    agent_run,
    current_context,
    use_context,
)
from tradingagents.core.model_policy import freeze_model_config
from tradingagents.core.persistence import Database
from tradingagents.skills.base import BaseSkill, SkillEvent, skill_stream

logger = logging.getLogger(__name__)


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Run:
    """Represents a single Skill execution."""

    id: str
    skill_id: str
    params: dict[str, Any]
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[SkillEvent] = field(default_factory=list)
    _task: asyncio.Task | None = field(default=None, repr=False)
    _skill: BaseSkill | None = field(default=None, repr=False)
    # Monotonic per-run event counter for persisted event ordering.
    _event_seq: int = field(default=0, repr=False)


class RunManager:
    """Manages all run lifecycles.

    Each run is an asyncio.Task. Subscribers receive events via
    per-run asyncio.Queue instances.
    """

    def __init__(
        self,
        db: Database | None = None,
        max_concurrent_runs: int = 3,
        max_retained_runs: int = 200,
        max_events_per_run: int = 500,
    ) -> None:
        self._contexts: dict[str, AgentContext] = {}
        self._runs: dict[str, Run] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._active_fingerprints: dict[str, str] = {}
        self._run_fingerprints: dict[str, str] = {}
        self._run_slots = asyncio.Semaphore(max(1, int(max_concurrent_runs)))
        self._max_retained_runs = max(10, int(max_retained_runs))
        self._max_events_per_run = max(50, int(max_events_per_run))
        self._db = db
        if self._db is not None:
            self._db.reconcile_interrupted_runs()
            self._db.reconcile_agent_runtime()

    async def create_run(
        self,
        skill: BaseSkill,
        params: dict[str, Any],
        config: dict[str, Any],
        *,
        deduplicate: bool = True,
    ) -> Run:
        """Create and start a run; callers with independent cancellation may opt out of reuse."""
        fingerprint = None
        if deduplicate:
            fingerprint = hashlib.sha256(
                json.dumps(
                    {"skill_id": skill.metadata.id, "params": params},
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                ).encode()
            ).hexdigest()
            existing_id = self._active_fingerprints.get(fingerprint)
            if existing_id:
                existing = self._runs.get(existing_id)
                if existing and existing.status in {RunStatus.PENDING, RunStatus.RUNNING}:
                    return existing
        run = Run(
            id=str(uuid.uuid4()),
            skill_id=skill.metadata.id,
            params=params,
            _skill=skill,
        )
        self._runs[run.id] = run
        self._prune_memory()
        if fingerprint is not None:
            self._active_fingerprints[fingerprint] = run.id
            self._run_fingerprints[run.id] = fingerprint
        self._subscribers[run.id] = []
        await self._save_run(run)

        run._task = asyncio.create_task(
            self._execute(run, skill, params, freeze_model_config(config))
        )
        return run

    async def _execute(
        self,
        run: Run,
        skill: BaseSkill,
        params: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        """Execute the skill and broadcast events to subscribers."""
        loop = asyncio.get_running_loop()
        pending_events = set()
        accepting = True
        parent_context = current_context()
        def dispatch(record):
            if not accepting:
                return
            pending = asyncio.create_task(self._record_event(run, run.id, SkillEvent(event_type="agent_runtime", data=record)))
            pending_events.add(pending)
        def emit(record):
            if parent_context and parent_context.emit:
                parent_context.emit(record)
            if accepting:
                loop.call_soon_threadsafe(dispatch, record)
        try:
            async with self._run_slots:
                parent = replace(parent_context, emit=emit) if parent_context else AgentContext.root(config, root_id=run.id, db=self._db, emit=emit)
                with agent_run(AgentSpec(skill.metadata.id), parent, kind="workflow") as child:
                    context = replace(current_context(), host_run_id=run.id, cancel_scope=threading.Event())
                    self._contexts[run.id] = context
                    with use_context(context):
                        await self._execute_in_slot(run, skill, params, config)
                    child.status = run.status.value
                    child.output = run.result or {}
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = str(exc)
            run.completed_at = datetime.now(timezone.utc)
            await self._update_run(run)
        finally:
            await asyncio.sleep(0)
            if pending_events:
                await asyncio.gather(*pending_events, return_exceptions=True)
            accepting = False
            if run.status in {RunStatus.PENDING, RunStatus.RUNNING}:
                run.status = RunStatus.CANCELLED
                run.completed_at = datetime.now(timezone.utc)
                await self._update_run(run)
            event = (SkillEvent(event_type="run_complete", data={"status": run.status.value, "result": run.result or {}})
                     if run.status == RunStatus.COMPLETED else
                     SkillEvent(event_type="run_cancelled", data={"status": run.status.value})
                     if run.status == RunStatus.CANCELLED else
                     SkillEvent(event_type="error", data={"message": run.error or "Run failed"}))
            await self._record_event(run, run.id, event)
            self._contexts.pop(run.id, None)
            # Admission deduplication must never remain stuck after an
            # unexpected infrastructure error or cancellation.
            self._release_fingerprint(run.id)
            duration_ms = (
                int((run.completed_at - run.started_at).total_seconds() * 1000)
                if run.started_at and run.completed_at else None
            )
            logger.info(
                "run_finished skill=%s run_id=%s status=%s duration_ms=%s events=%d",
                run.skill_id, run.id, run.status.value, duration_ms, len(run.events),
            )

    def _release_fingerprint(self, run_id: str) -> None:
        fingerprint = self._run_fingerprints.pop(run_id, None)
        if fingerprint and self._active_fingerprints.get(fingerprint) == run_id:
            self._active_fingerprints.pop(fingerprint, None)

    async def _finalize_prestart_cancellation(self, run: Run) -> None:
        # asyncio does not enter _execute when its task is cancelled before
        # its first instruction. Persist the terminal state ourselves.
        if (not isinstance(run, Run) or run._task is None or not run._task.done() or
                not run._task.cancelled() or run.status != RunStatus.PENDING):
            return
        run.status = RunStatus.CANCELLED
        run.completed_at = datetime.now(timezone.utc)
        self._release_fingerprint(run.id)
        await self._update_run(run)
        await self._record_event(
            run, run.id,
            SkillEvent(event_type="run_cancelled", data={"status": run.status.value}),
        )

    async def _execute_in_slot(self, run, skill, params, config) -> None:
        """Execute a validated template; outer runtime publishes the terminal event."""
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        await self._update_run(run)
        try:
            validated_params = skill.validate_params(params)
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = f"Validation error: {exc}"
            run.completed_at = datetime.now(timezone.utc)
            await self._update_run(run)
            return
        try:
            saw_skill_complete = False
            run_config = {**config, "run_id": run.id}
            if self._db is not None:
                run_config["db"] = self._db
            async for event in skill_stream(skill, validated_params, run_config):
                if event.event_type == "skill_complete":
                    saw_skill_complete = True
                    await self._record_event(run, run.id, event)
                    if str(event.data.get("status") or "").lower() != "success":
                        raise RuntimeError(str(event.data.get("error") or "Skill reported a non-success result"))
                    skill.validate_output(event.data)
                    run.result = event.data
                    continue
                if event.event_type == "run_complete":
                    raise ValueError("Skills must emit skill_complete, not run_complete")
                await self._record_event(run, run.id, event)
            if not saw_skill_complete:
                raise RuntimeError("Skill ended without a skill_complete event")
            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            await self._update_run(run)
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            run.completed_at = datetime.now(timezone.utc)
            await self._update_run(run)
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            run.completed_at = datetime.now(timezone.utc)
            await self._update_run(run)

    async def _record_event(self, run: Run, run_id: str, event: SkillEvent) -> None:
        """Persist an event in memory, to subscribers, and (best-effort) to the
        DB so a reconnecting client can replay progress after a server restart."""
        run.events.append(event)
        run._event_seq += 1
        if len(run.events) > self._max_events_per_run:
            del run.events[: len(run.events) - self._max_events_per_run]
        # Persist for replay-on-reconnect. Best-effort; never block the pipeline.
        if self._db is not None and hasattr(self._db, "save_run_event"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self._db.save_run_event,
                    run_id, run._event_seq, event.event_type, event.data,
                )
        await self._broadcast(run_id, event)

    async def _broadcast(self, run_id: str, event: SkillEvent) -> None:
        """Broadcast an event to all subscribers of a run.

        Uses ``put_nowait`` with drop-oldest semantics so a slow subscriber
        cannot unboundedly grow memory: if a per-run queue is full, the oldest
        queued event is discarded to make room for the newer one. Terminal
        events (run_complete / run_cancelled / error) always force their way in
        so clients reliably see the final state.
        """
        for queue in self._subscribers.get(run_id, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest event to make room for the newer one.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

    async def _save_run(self, run: Run) -> None:
        """Save a run record if persistence is configured."""
        if self._db is None:
            return
        await asyncio.to_thread(
            self._db.save_run, run.id, run.skill_id, run.params, run.status.value
        )

    async def _update_run(self, run: Run) -> None:
        """Update a run record if persistence is configured."""
        if self._db is None:
            return
        write = asyncio.create_task(asyncio.to_thread(
            self._db.update_run_status,
            run.id,
            run.status.value,
            result=run.result,
            error=run.error,
            started_at=run.started_at.isoformat() if run.started_at else None,
            completed_at=run.completed_at.isoformat() if run.completed_at else None,
        ))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            # Cancelling to_thread does not stop its worker. Drain this write
            # before cleanup persists CANCELLED, so RUNNING cannot arrive last.
            await write
            raise

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """Subscribe to a run's event stream.

        The queue is bounded so a slow client cannot grow memory unboundedly;
        ``_broadcast`` drops the oldest event when full.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        """Unsubscribe from a run's event stream."""
        if run_id in self._subscribers:
            self._subscribers[run_id] = [
                q for q in self._subscribers[run_id] if q is not queue
            ]
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)

    def _prune_memory(self) -> None:
        """Evict oldest terminal runs; persisted history remains queryable."""
        overflow = len(self._runs) - self._max_retained_runs
        if overflow <= 0:
            return
        terminal = sorted(
            (
                run for run in self._runs.values()
                if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
            ),
            key=lambda run: run.completed_at or run.created_at,
        )
        for run in terminal[:overflow]:
            self._runs.pop(run.id, None)
            self._subscribers.pop(run.id, None)

    def get_run(self, run_id: str) -> Run | None:
        """Get a run by ID."""
        run = self._runs.get(run_id)
        if run is not None or self._db is None:
            return run
        record = self._db.get_run(run_id)
        if record is None:
            return None
        run = self._run_from_record(record)
        self._runs[run.id] = run
        return run

    async def wait_for_run(self, run_id: str, timeout: float | None = None) -> Run:
        """Wait for an in-process run to reach a terminal state."""
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        if run._task is not None and not run._task.done():
            waiter = asyncio.shield(run._task)
            if timeout is None:
                await waiter
            else:
                await asyncio.wait_for(waiter, timeout=timeout)
        return self.get_run(run_id) or run

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[Run]:
        """List recent runs with pagination."""
        runs_by_id = dict(self._runs)
        if self._db is not None:
            for record in self._db.list_runs(limit=limit, offset=offset):
                if record["id"] not in runs_by_id:
                    runs_by_id[record["id"]] = self._run_from_record(record)
        runs = sorted(runs_by_id.values(), key=lambda r: r.created_at, reverse=True)
        return runs[:limit]

    async def cancel_run(self, run_id: str) -> bool:
        """Cancel a running task."""
        run = self._runs.get(run_id)
        if run and run._task and not run._task.done():
            if context := self._contexts.get(run_id):
                context.cancel_scope.set()
            if run._skill is not None:
                # A broken cooperative hook must not prevent cancellation of
                # the owning asyncio task.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(run._skill.cancel(), timeout=2.0)
            run._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(run._task, timeout=2.0)
            await self._finalize_prestart_cancellation(run)
            return True
        return False

    async def cancel_all(self) -> None:
        """Cancel all running tasks (used on app shutdown).

        Waits for the cancelled tasks to actually finish (with a short timeout)
        so in-flight DB/file writes complete or raise CancelledError cleanly,
        instead of being hard-killed mid-write which can leave half-written
        SQLite rows or report files.
        """
        tasks: list[asyncio.Task] = []
        cancelled_runs: list[Run] = []
        for run in self._runs.values():
            if run._task and not run._task.done():
                if run._skill is not None:
                    with contextlib.suppress(Exception):
                        await run._skill.cancel()
                run._task.cancel()
                tasks.append(run._task)
                cancelled_runs.append(run)
        if tasks:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=5.0,
            )
            for run in cancelled_runs:
                await self._finalize_prestart_cancellation(run)

    def _run_from_record(self, record: dict[str, Any]) -> Run:
        """Convert a persisted SQLite row into a Run object."""
        return Run(
            id=record["id"],
            skill_id=record["skill_id"],
            params=_decode_json_object(record.get("params"), {}),
            status=_decode_status(record.get("status")),
            created_at=_decode_datetime(record.get("created_at")) or datetime.now(timezone.utc),
            started_at=_decode_datetime(record.get("started_at")),
            completed_at=_decode_datetime(record.get("completed_at")),
            result=_decode_json_object(record.get("result"), None),
            error=record.get("error"),
        )


def _decode_json_object(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _decode_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _decode_status(value: Any) -> RunStatus:
    try:
        return RunStatus(str(value))
    except ValueError:
        return RunStatus.FAILED
