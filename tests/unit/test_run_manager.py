"""Unit tests for the Run Manager."""

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator

from pydantic import BaseModel

from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager, RunStatus
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


class RunInput(BaseModel):
    value: str = "hello"


class RunTestSkill(BaseSkill):
    @property
    def metadata(self):
        return SkillMetadata(
            id="test", name="Test", description="test", version="0.1.0"
        )

    @property
    def input_schema(self):
        return RunInput

    @property
    def output_schema(self):
        return RunInput

    async def execute(self, params, config) -> AsyncIterator[SkillEvent]:
        yield SkillEvent(event_type="agent_status", data={"msg": "starting"})
        await asyncio.sleep(0.01)
        yield SkillEvent(event_type="skill_complete", data={"status": "success", "value": params.value})

    async def cancel(self):
        pass


def test_create_and_complete_run():
    async def run():
        manager = RunManager()
        skill = RunTestSkill()
        r = await manager.create_run(skill, {"value": "test"}, {})
        assert r.skill_id == "test"
        await asyncio.sleep(0.3)
        completed = manager.get_run(r.id)
        assert completed.status == RunStatus.COMPLETED
        assert [e.event_type for e in completed.events if e.event_type != "agent_runtime"] == [
            "agent_status",
            "skill_complete",
            "run_complete",
        ]

    asyncio.run(run())


def test_run_manager_accepts_stock_analysis_graph_events():
    """Raw graph events consumed by the Analysis page remain valid Skill events."""

    class GraphEventSkill(RunTestSkill):
        async def execute(self, params, config) -> AsyncIterator[SkillEvent]:
            yield SkillEvent(
                event_type="tool_call",
                data={"tool": "get_verified_market_snapshot", "args": {"symbol": "603341.SH"}},
            )
            yield SkillEvent(
                event_type="report_complete",
                data={"sections": {"final_trade_decision": "HOLD"}},
            )
            yield SkillEvent(
                event_type="skill_complete",
                data={"status": "success", "value": params.value},
            )

    async def run():
        manager = RunManager()
        result = await manager.create_run(GraphEventSkill(), {"value": "test"}, {})
        await asyncio.wait_for(result._task, timeout=1.0)

        completed = manager.get_run(result.id)
        assert completed.status == RunStatus.COMPLETED
        assert [event.event_type for event in completed.events if event.event_type != "agent_runtime"] == [
            "tool_call",
            "report_complete",
            "skill_complete",
            "run_complete",
        ]

    asyncio.run(run())


def test_list_runs():
    async def run():
        manager = RunManager()
        skill = RunTestSkill()
        await manager.create_run(skill, {"value": "one"}, {})
        await manager.create_run(skill, {"value": "two"}, {})
        await asyncio.sleep(0.2)
        runs = manager.list_runs()
        assert len(runs) == 2

    asyncio.run(run())


def test_subscribe_and_receive():
    async def run():
        manager = RunManager()
        skill = RunTestSkill()
        r = await manager.create_run(skill, {}, {})
        queue = manager.subscribe(r.id)
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        while event.event_type == "agent_runtime":
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.event_type == "agent_status"
        manager.unsubscribe(r.id, queue)

    asyncio.run(run())


def test_cancel_run():
    cancelled = False

    class SlowSkill(BaseSkill):
        @property
        def metadata(self):
            return SkillMetadata(id="slow", name="Slow", description="", version="0.1")

        @property
        def input_schema(self):
            return RunInput

        @property
        def output_schema(self):
            return RunInput

        async def execute(self, params, config) -> AsyncIterator[SkillEvent]:
            await asyncio.sleep(10)
            yield SkillEvent(event_type="never", data={})

        async def cancel(self):
            nonlocal cancelled
            cancelled = True

    async def run():
        manager = RunManager()
        r = await manager.create_run(SlowSkill(), {}, {})
        await asyncio.sleep(0.05)
        result = await manager.cancel_run(r.id)
        assert result is True
        await asyncio.sleep(0.1)
        assert manager.get_run(r.id).status == RunStatus.CANCELLED
        assert cancelled is True

    asyncio.run(run())


def test_deduplication_can_be_disabled_for_independently_cancelled_runs(tmp_path):
    async def run():
        db = Database(tmp_path / "independent-runs.db")
        manager = RunManager(db=db)
        skill = RunTestSkill()
        first = await manager.create_run(skill, {"value": "same"}, {}, deduplicate=False)
        await manager.cancel_run(first.id)
        assert db.get_run(first.id)["status"] == RunStatus.CANCELLED.value
        assert db.list_run_events(first.id)[-1]["event_type"] == "run_cancelled"

        second = await manager.create_run(skill, {"value": "same"}, {}, deduplicate=False)
        shared = await manager.create_run(skill, {"value": "shared"}, {})
        reused = await manager.create_run(skill, {"value": "shared"}, {})

        assert first.id != second.id
        assert shared.id == reused.id
        await manager.wait_for_run(second.id)
        assert manager.get_run(first.id).status == RunStatus.CANCELLED
        assert manager.get_run(second.id).status == RunStatus.COMPLETED

    asyncio.run(run())


def test_cancelled_status_write_finishes_before_terminal_cleanup(tmp_path):
    async def run():
        db = Database(tmp_path / "ordered-cancellation.db")
        manager = RunManager(db=db)
        db.save_run("ordered", "test", {}, "pending")
        record = manager.get_run("ordered")
        record.status = RunStatus.RUNNING
        started = threading.Event()
        release = threading.Event()
        original = db.update_run_status

        def delayed_update(*args, **kwargs):
            started.set()
            assert release.wait(timeout=5)
            original(*args, **kwargs)

        db.update_run_status = delayed_update
        write = asyncio.create_task(manager._update_run(record))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            write.cancel()
            for _ in range(3):
                await asyncio.sleep(0)
            assert not write.done(), "Cancellation must drain the in-flight database worker"
        finally:
            release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await write
            db.update_run_status = original
        record.status = RunStatus.CANCELLED
        await manager._update_run(record)
        assert db.get_run(record.id)["status"] == "cancelled"

    asyncio.run(run())


def test_run_manager_persists_run_lifecycle(tmp_path):
    async def run():
        db = Database(tmp_path / "runs.db")
        manager = RunManager(db=db)
        skill = RunTestSkill()
        r = await manager.create_run(skill, {"value": "persisted"}, {})

        initial = db.get_run(r.id)
        assert initial is not None
        assert initial["status"] == RunStatus.PENDING.value

        await asyncio.sleep(0.3)
        stored = db.get_run(r.id)
        assert stored is not None
        assert stored["status"] == RunStatus.COMPLETED.value
        assert stored["started_at"] is not None
        assert stored["completed_at"] is not None

    asyncio.run(run())


def test_run_manager_reconciles_interrupted_persisted_run(tmp_path):
    db = Database(tmp_path / "interrupted.db")
    db.save_run("zombie", "test", {}, "pending")
    db.update_run_status("zombie", "running", started_at="2026-07-18T00:00:00Z")

    RunManager(db=db)

    stored = db.get_run("zombie")
    assert stored["status"] == "failed"
    assert stored["error"] == "Interrupted by application restart"
    events = db.list_run_events("zombie")
    assert events[-1]["event_type"] == "error"
    assert events[-1]["payload"]["message"] == "Interrupted by application restart"
