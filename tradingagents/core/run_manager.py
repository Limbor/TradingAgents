"""Run lifecycle management.

A Run represents a single execution of a Skill. The RunManager creates,
tracks, and cancels runs, broadcasting events to subscribers (WebSocket
endpoints) via per-run asyncio Queues.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from tradingagents.core.persistence import Database
from tradingagents.skills.base import BaseSkill, SkillEvent


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


class RunManager:
    """Manages all run lifecycles.

    Each run is an asyncio.Task. Subscribers receive events via
    per-run asyncio.Queue instances.
    """

    def __init__(self, db: Database | None = None) -> None:
        self._runs: dict[str, Run] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._db = db

    async def create_run(
        self,
        skill: BaseSkill,
        params: dict[str, Any],
        config: dict[str, Any],
    ) -> Run:
        """Create and start a new run."""
        run = Run(
            id=str(uuid.uuid4()),
            skill_id=skill.metadata.id,
            params=params,
            _skill=skill,
        )
        self._runs[run.id] = run
        self._subscribers[run.id] = []
        self._save_run(run)

        run._task = asyncio.create_task(self._execute(run, skill, params, config))
        return run

    async def _execute(
        self,
        run: Run,
        skill: BaseSkill,
        params: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        """Execute the skill and broadcast events to subscribers."""
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        self._update_run(run)

        try:
            validated_params = skill.validate_params(params)
        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = f"Validation error: {e}"
            run.completed_at = datetime.now(timezone.utc)
            self._update_run(run)
            await self._record_event(
                run,
                run.id,
                SkillEvent(event_type="error", data={"message": str(e)}),
            )
            return

        try:
            run_config = {**config, "run_id": run.id}
            if self._db is not None:
                run_config["db"] = self._db

            async for event in skill.execute(validated_params, run_config):
                if event.event_type in {"skill_complete", "run_complete"}:
                    run.result = event.data
                await self._record_event(run, run.id, event)

            run.status = RunStatus.COMPLETED
            run.completed_at = datetime.now(timezone.utc)
            self._update_run(run)
            await self._record_event(
                run,
                run.id,
                SkillEvent(
                    event_type="run_complete",
                    data={
                        "status": run.status.value,
                        "result": run.result or {},
                    },
                ),
            )
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            run.completed_at = datetime.now(timezone.utc)
            self._update_run(run)
            await self._record_event(
                run,
                run.id,
                SkillEvent(event_type="run_cancelled", data={"status": run.status.value}),
            )
        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = str(e)
            run.completed_at = datetime.now(timezone.utc)
            self._update_run(run)
            await self._record_event(
                run,
                run.id,
                SkillEvent(event_type="error", data={"message": str(e)}),
            )

    async def _record_event(self, run: Run, run_id: str, event: SkillEvent) -> None:
        """Persist an event in memory and broadcast it to subscribers."""
        run.events.append(event)
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
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Still full (concurrent producer); drop this event rather
                    # than block the run loop.
                    pass

    def _save_run(self, run: Run) -> None:
        """Save a run record if persistence is configured."""
        if self._db is None:
            return
        self._db.save_run(run.id, run.skill_id, run.params, run.status.value)

    def _update_run(self, run: Run) -> None:
        """Update a run record if persistence is configured."""
        if self._db is None:
            return
        self._db.update_run_status(
            run.id,
            run.status.value,
            result=run.result,
            error=run.error,
            started_at=run.started_at.isoformat() if run.started_at else None,
            completed_at=run.completed_at.isoformat() if run.completed_at else None,
        )

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

    def list_runs(self, limit: int = 50) -> list[Run]:
        """List recent runs."""
        runs_by_id = dict(self._runs)
        if self._db is not None:
            for record in self._db.list_runs(limit=limit):
                if record["id"] not in runs_by_id:
                    runs_by_id[record["id"]] = self._run_from_record(record)
        runs = sorted(runs_by_id.values(), key=lambda r: r.created_at, reverse=True)
        return runs[:limit]

    async def cancel_run(self, run_id: str) -> bool:
        """Cancel a running task."""
        run = self._runs.get(run_id)
        if run and run._task and not run._task.done():
            if run._skill is not None:
                await run._skill.cancel()
            run._task.cancel()
            try:
                await asyncio.wait_for(run._task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
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
        for run in self._runs.values():
            if run._task and not run._task.done():
                if run._skill is not None:
                    try:
                        await run._skill.cancel()
                    except Exception:
                        pass
                run._task.cancel()
                tasks.append(run._task)
        if tasks:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=5.0,
            )

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
