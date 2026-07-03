"""Lightweight asyncio scheduler for local background jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

ScheduledJob = Callable[[], Awaitable[Any]]


@dataclass
class ScheduledTask:
    name: str
    run_at: time
    job: ScheduledJob
    task: asyncio.Task | None = None
    last_run_at: str | None = None
    error: str | None = None


class Scheduler:
    """Single-process daily scheduler.

    It is intentionally small: suitable for a desktop/local app where FastAPI
    owns the process.  Jobs are best-effort; failures are recorded and do not
    crash the server.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._stopped = asyncio.Event()

    def register_daily(self, name: str, run_at: time, job: ScheduledJob) -> None:
        self._tasks[name] = ScheduledTask(name=name, run_at=run_at, job=job)

    def start(self) -> None:
        self._stopped.clear()
        for item in self._tasks.values():
            if item.task is None or item.task.done():
                item.task = asyncio.create_task(self._run_daily(item))

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
                "running": bool(item.task and not item.task.done()),
            }
            for item in self._tasks.values()
        ]

    async def _run_daily(self, item: ScheduledTask) -> None:
        while not self._stopped.is_set():
            delay = _seconds_until(item.run_at)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass

            try:
                await item.job()
                item.last_run_at = datetime.now(timezone.utc).isoformat()
                item.error = None
            except Exception as exc:
                item.error = f"{type(exc).__name__}: {exc}"
                logger.exception("Scheduled job %s failed", item.name)


def _seconds_until(target: time) -> float:
    now = datetime.now()
    next_run = now.replace(
        hour=target.hour,
        minute=target.minute,
        second=target.second,
        microsecond=0,
    )
    if next_run <= now:
        next_run = next_run + timedelta(days=1)
    return max(1.0, (next_run - now).total_seconds())
