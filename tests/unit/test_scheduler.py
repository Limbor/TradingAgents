import asyncio
from datetime import datetime, time

from tradingagents.core.persistence import Database
from tradingagents.core.scheduler import SHANGHAI_TZ, Scheduler, _should_catch_up


def test_daily_catchup_only_runs_once_and_retries_crashed_state():
    now = datetime(2026, 7, 20, 10, 0, tzinfo=SHANGHAI_TZ)
    target = time(8, 30)
    assert _should_catch_up(target, now, None) is True
    assert _should_catch_up(
        target, now, {"last_scheduled_date": "2026-07-20", "status": "completed"}
    ) is False
    assert _should_catch_up(
        target, now, {"last_scheduled_date": "2026-07-20", "status": "failed"}
    ) is False
    assert _should_catch_up(
        target, now, {"last_scheduled_date": "2026-07-20", "status": "running"}
    ) is True


def test_scheduler_persists_completion_across_instances(tmp_path):
    async def run():
        db = Database(tmp_path / "scheduler.db")
        calls = 0

        async def job():
            nonlocal calls
            calls += 1

        scheduler = Scheduler(db=db)
        scheduler.register_daily("daily", time(8, 30), job)
        item = scheduler._tasks["daily"]
        await scheduler._execute_job(item, scheduled_date="2026-07-20")
        assert calls == 1
        state = db.get_scheduler_job_state("daily")
        assert state["status"] == "completed"
        assert state["last_scheduled_date"] == "2026-07-20"

        restored = Scheduler(db=db)
        restored.register_daily("daily", time(8, 30), job)
        restored_item = restored._tasks["daily"]
        assert restored_item.status == "completed"
        assert restored_item.last_scheduled_date == "2026-07-20"
        assert restored_item.last_run_at is not None

    asyncio.run(run())
