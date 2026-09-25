"""Lightweight asyncio scheduler for local background jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ScheduledJob = Callable[[], Awaitable[Any]]

# A-share market jobs are timed against Asia/Shanghai. Using the system local
# timezone (the previous naive ``datetime.now()``) made daily jobs fire at the
# wrong wall-clock time on hosts whose system clock is UTC (e.g. Docker/CI).
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class ScheduledTask:
    name: str
    run_at: time
    job: ScheduledJob
    task: asyncio.Task | None = None
    last_run_at: str | None = None
    error: str | None = None
    last_scheduled_date: str | None = None
    status: str = "idle"
    catch_up_window: timedelta | None = None
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 2
    retry_delay_seconds: float = 2.0


class Scheduler:
    """Single-process daily scheduler.

    It is intentionally small: suitable for a desktop/local app where FastAPI
    owns the process.  Jobs are best-effort; failures are recorded and do not
    crash the server.
    """

    def __init__(self, db: Any | None = None) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._stopped = asyncio.Event()
        self._db = db

    def register_daily(
        self,
        name: str,
        run_at: time,
        job: ScheduledJob,
        *,
        catch_up_window: timedelta | None = None,
        depends_on: tuple[str, ...] = (),
        max_attempts: int = 2,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        item = ScheduledTask(
            name=name,
            run_at=run_at,
            job=job,
            catch_up_window=catch_up_window,
            depends_on=depends_on,
            max_attempts=max(1, max_attempts),
            retry_delay_seconds=max(0.0, retry_delay_seconds),
        )
        state = self._job_state(name)
        if state:
            item.last_run_at = state.get("last_completed_at")
            item.error = state.get("error")
            item.last_scheduled_date = state.get("last_scheduled_date")
            item.status = str(state.get("status") or "idle")
        self._tasks[name] = item

    def register_interval(self, name: str, interval_seconds: float, job: ScheduledJob) -> None:
        """Register a job that repeats every *interval_seconds*.

        Internally stored as a ScheduledTask with run_at=None; the _run_interval
        loop handles timing.
        """
        task = ScheduledTask(name=name, run_at=time(0, 0), job=job)
        task._interval = interval_seconds  # type: ignore[attr-defined]
        self._tasks[name] = task

    def start(self) -> None:
        self._stopped.clear()
        for item in self._tasks.values():
            if item.task is None or item.task.done():
                if hasattr(item, "_interval"):
                    item.task = asyncio.create_task(self._run_interval(item))
                else:
                    item.task = asyncio.create_task(self._run_daily(item))

    def is_running(self) -> bool:
        """True if the scheduler has live task loops (not stopped)."""
        return not self._stopped.is_set() and any(
            item.task is not None and not item.task.done()
            for item in self._tasks.values()
        )

    async def stop(self) -> None:
        self._stopped.set()
        for item in self._tasks.values():
            if item.task and not item.task.done():
                item.task.cancel()
        await asyncio.gather(
            *(item.task for item in self._tasks.values() if item.task),
            return_exceptions=True,
        )

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "run_at": item.run_at.isoformat(timespec="minutes"),
                "last_run_at": item.last_run_at,
                "error": item.error,
                "status": item.status,
                "last_scheduled_date": item.last_scheduled_date,
                "running": bool(item.task and not item.task.done()),
            }
            for item in self._tasks.values()
        ]

    async def _run_daily(self, item: ScheduledTask) -> None:
        while not self._stopped.is_set():
            now = datetime.now(SHANGHAI_TZ)
            state = self._job_state(item.name)
            if _should_catch_up(item.run_at, now, state, item.catch_up_window):
                await self._execute_job(item, scheduled_date=now.date().isoformat())
                continue
            delay = _seconds_until(item.run_at)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass

            scheduled_date = datetime.now(SHANGHAI_TZ).date().isoformat()
            await self._execute_job(item, scheduled_date=scheduled_date)


    async def _run_interval(self, item: ScheduledTask) -> None:
        interval = getattr(item, "_interval", 3600)
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

            await self._execute_job(item, scheduled_date=None)

    def _job_state(self, name: str) -> dict[str, Any] | None:
        if self._db is not None and hasattr(self._db, "get_scheduler_job_state"):
            persisted = self._db.get_scheduler_job_state(name)
            if persisted:
                return persisted
        item = self._tasks.get(name)
        if item and item.last_scheduled_date:
            return {
                "last_scheduled_date": item.last_scheduled_date,
                "status": item.status,
            }
        return None

    async def _execute_job(
        self, item: ScheduledTask, *, scheduled_date: str | None
    ) -> None:
        if scheduled_date is not None:
            await self._wait_for_dependencies(item, scheduled_date)
        started_at = datetime.now(timezone.utc).isoformat()
        item.last_scheduled_date = scheduled_date or item.last_scheduled_date
        item.status = "running"
        if self._db is not None and hasattr(self._db, "save_scheduler_job_state"):
            self._db.save_scheduler_job_state(
                item.name, scheduled_date=scheduled_date, status="running",
                started_at=started_at, error=None,
            )
        last_error: Exception | None = None
        for attempt in range(1, item.max_attempts + 1):
            try:
                await item.job()
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger.exception(
                    "Scheduled job %s attempt %d/%d failed",
                    item.name, attempt, item.max_attempts,
                )
                if attempt < item.max_attempts:
                    await asyncio.sleep(item.retry_delay_seconds * attempt)
        if last_error is None:
            item.last_run_at = datetime.now(timezone.utc).isoformat()
            item.error = None
            item.status = "completed"
            if self._db is not None and hasattr(self._db, "save_scheduler_job_state"):
                self._db.save_scheduler_job_state(
                    item.name, scheduled_date=scheduled_date, status="completed",
                    completed_at=item.last_run_at, error=None,
                )
        else:
            item.error = f"{type(last_error).__name__}: {last_error}"
            item.status = "failed"
            if self._db is not None and hasattr(self._db, "save_scheduler_job_state"):
                self._db.save_scheduler_job_state(
                    item.name, scheduled_date=scheduled_date, status="failed",
                    completed_at=datetime.now(timezone.utc).isoformat(), error=item.error,
                )

    async def _wait_for_dependencies(
        self, item: ScheduledTask, scheduled_date: str
    ) -> None:
        """Wait until every same-day predecessor has completed successfully."""
        for dependency in item.depends_on:
            if dependency not in self._tasks:
                raise RuntimeError(f"Unknown scheduler dependency: {dependency}")
            for _ in range(600):
                state = self._job_state(dependency)
                if state and state.get("last_scheduled_date") == scheduled_date:
                    if state.get("status") == "completed":
                        break
                    if state.get("status") == "failed":
                        raise RuntimeError(f"Dependency failed: {dependency}")
                await asyncio.sleep(1)
            else:
                raise TimeoutError(f"Dependency did not complete: {dependency}")


def _should_catch_up(
    target: time,
    now: datetime,
    state: dict[str, Any] | None,
    catch_up_window: timedelta | None = None,
) -> bool:
    """Run today's missed daily slot once; retry only a crash-marked running job."""
    scheduled = now.replace(
        hour=target.hour, minute=target.minute, second=target.second, microsecond=0
    )
    if now < scheduled:
        return False
    if catch_up_window is not None and now - scheduled > catch_up_window:
        return False
    if not state or state.get("last_scheduled_date") != now.date().isoformat():
        return True
    return state.get("status") == "running"


def _seconds_until(target: time) -> float:
    # Interpret ``target`` as a Shanghai wall-clock time, regardless of the host
    # system timezone, so daily_pipeline at 08:30 fires at 08:30 Asia/Shanghai.
    now = datetime.now(SHANGHAI_TZ)
    next_run = now.replace(
        hour=target.hour,
        minute=target.minute,
        second=target.second,
        microsecond=0,
    )
    if next_run <= now:
        next_run = next_run + timedelta(days=1)
    return max(1.0, (next_run - now).total_seconds())
