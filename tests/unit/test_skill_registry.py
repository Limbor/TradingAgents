"""Unit tests for the Skill Registry."""

from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel, ValidationError

from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata
from tradingagents.skills.registry import SkillRegistry


class MockInput(BaseModel):
    ticker: str


class MockOutput(BaseModel):
    result: str


class MockSkill(BaseSkill):
    @property
    def metadata(self):
        return SkillMetadata(
            id="mock",
            name="Mock Skill",
            description="A mock skill for testing",
            version="0.1.0",
            triggers=["test", "mock"],
        )

    @property
    def input_schema(self):
        return MockInput

    @property
    def output_schema(self):
        return MockOutput

    async def execute(self, params, config) -> AsyncIterator[SkillEvent]:
        yield SkillEvent(event_type="done", data={"result": "ok"})

    async def cancel(self):
        pass


def test_register_and_get():
    registry = SkillRegistry()
    skill = MockSkill()
    registry.register(skill)
    assert registry.get("mock") is skill


def test_get_nonexistent():
    registry = SkillRegistry()
    assert registry.get("nonexistent") is None


def test_duplicate_registration_raises():
    registry = SkillRegistry()
    skill = MockSkill()
    registry.register(skill)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(skill)


def test_find_by_trigger():
    registry = SkillRegistry()
    registry.register(MockSkill())
    matches = registry.find_by_trigger("run a test analysis")
    assert len(matches) == 1
    assert matches[0].metadata.id == "mock"


def test_find_by_trigger_no_match():
    registry = SkillRegistry()
    registry.register(MockSkill())
    matches = registry.find_by_trigger("completely unrelated")
    assert len(matches) == 0


def test_list_all():
    registry = SkillRegistry()
    registry.register(MockSkill())
    metadata = registry.list_all()
    assert len(metadata) == 1
    assert metadata[0].id == "mock"


def test_auto_discover():
    registry = SkillRegistry()
    registry.auto_discover()
    skills = registry.list_all()
    assert len(skills) >= 1
    skill_ids = [s.id for s in skills]
    assert "stock_analysis" in skill_ids


def test_validate_params():
    skill = MockSkill()
    params = skill.validate_params({"ticker": "AAPL"})
    assert params.ticker == "AAPL"


def test_validate_params_invalid():
    skill = MockSkill()
    with pytest.raises(ValidationError):
        skill.validate_params({})  # Missing required 'ticker'
