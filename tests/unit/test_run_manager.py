"""Unit tests for the Run Manager."""

import asyncio
from typing import Any, AsyncIterator

from pydantic import BaseModel

from tradingagents.core.persistence import Database
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata
from tradingagents.core.run_manager import RunManager, RunStatus


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
        yield SkillEvent(event_type="step1", data={"msg": "starting"})
        await asyncio.sleep(0.01)
        yield SkillEvent(event_type="step2", data={"msg": "done"})

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
        assert [e.event_type for e in completed.events] == [
            "step1",
            "step2",
            "run_complete",
        ]

    asyncio.run(run())


def test_list_runs():
    async def run():
        manager = RunManager()
        skill = RunTestSkill()
        await manager.create_run(skill, {}, {})
        await manager.create_run(skill, {}, {})
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
        assert event.event_type == "step1"
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
