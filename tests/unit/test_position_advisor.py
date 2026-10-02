"""PositionAdvisor unit and routing tests."""

from __future__ import annotations

import asyncio
import importlib

from tradingagents.core.orchestrator import Orchestrator
from tradingagents.core.persistence import Database
from tradingagents.skills.position_advisor.skill import (
    PositionAdvisorInput,
    PositionAdvisorSkill,
    _decide,
    _execution_sizing,
)
from tradingagents.skills.registry import SkillRegistry


async def _complete(skill, params, config):
    events = [event async for event in skill.execute(params, config)]
    return events, [event for event in events if event.event_type == "skill_complete"][-1]


def test_position_advisor_reduces_overweight_holding(tmp_path, monkeypatch):
    async def no_mcp(config):
        return None

    advisor_module = importlib.import_module("tradingagents.skills.position_advisor.skill")
    risk_module = importlib.import_module("tradingagents.skills.risk_monitor.skill")
    monkeypatch.setattr(advisor_module, "get_mcp_client", no_mcp)
    monkeypatch.setattr(risk_module, "get_mcp_client", no_mcp)
    db = Database(tmp_path / "advisor.db")
    db.upsert_holding("600519.SH", 1000, 100, 110)
    db.upsert_holding("000001.SZ", 1000, 100, 100)

    events, complete = asyncio.run(
        _complete(
            PositionAdvisorSkill(),
            PositionAdvisorInput(symbol="600519.SH", max_position_pct=40),
            {"db": db, "run_id": "advisor-run", "stockmanager_mcp_enabled": False},
        )
    )

    assert complete.data["status"] == "success"
    assert complete.data["action"] == "REDUCE"
    assert complete.data["metrics"]["position_pct"] > 50
    assert complete.data["execution"]["quantity_change"] < 0
    assert complete.data["execution"]["quantity_change"] % 100 == 0
    assert complete.data["execution"]["target_position_pct"] <= 40
    assert complete.data["execution"]["requires_user_confirmation"] is True
    assert any(event.event_type == "position_advice" for event in events)
    artifacts = db.list_artifacts(skill_id="position_advisor")
    assert len(artifacts) == 1


def test_position_advisor_exits_on_critical_announcement(tmp_path, monkeypatch):
    class FakeClient:
        async def get_risk_announcements(self, *args, **kwargs):
            return {"rows": [{"title": "公司因违规事项被立案调查"}]}

        async def generate_trading_plan(self, **kwargs):
            return {"stop_loss": 88.0, "source": "stockmanager"}

    async def fake_mcp(config):
        return FakeClient()

    advisor_module = importlib.import_module("tradingagents.skills.position_advisor.skill")
    risk_module = importlib.import_module("tradingagents.skills.risk_monitor.skill")
    monkeypatch.setattr(advisor_module, "get_mcp_client", fake_mcp)
    monkeypatch.setattr(risk_module, "get_mcp_client", fake_mcp)
    db = Database(tmp_path / "critical.db")
    db.upsert_holding("600519.SH", 100, 100, 105)

    _, complete = asyncio.run(
        _complete(
            PositionAdvisorSkill(),
            PositionAdvisorInput(symbol="600519.SH"),
            {"db": db, "run_id": "critical-run"},
        )
    )

    assert complete.data["action"] == "EXIT"
    assert complete.data["risk"]["level"] == "red"
    assert complete.data["trading_plan"]["stop_loss"] == 88.0


def test_position_advisor_missing_holding_returns_error(tmp_path):
    db = Database(tmp_path / "missing.db")
    _, complete = asyncio.run(
        _complete(
            PositionAdvisorSkill(),
            PositionAdvisorInput(symbol="600519.SH"),
            {"db": db},
        )
    )
    assert complete.data == {
        "status": "error",
        "symbol": "600519.SH",
        "error": "holding_not_found",
    }


def test_trading_plan_stop_overrides_local_hold():
    action, confidence, reasons = _decide(
        "review",
        {"pnl_pct": -2.0, "position_pct": 20.0, "current_price": 89.0},
        {"level": "green"},
        stop_loss_pct=10,
        max_position_pct=30,
        trading_plan={"result": {"flight_plan": {"stop_loss": 90.0}}},
    )
    execution = _execution_sizing(
        action,
        {"quantity": 100.0, "position_pct": 20.0},
        max_position_pct=30,
    )

    assert action == "EXIT"
    assert confidence == 0.88
    assert "止损价" in reasons[0]
    assert execution["quantity_change"] == -100


def test_orchestrator_routes_position_advice():
    registry = SkillRegistry()
    registry.register(PositionAdvisorSkill())
    orchestrator = Orchestrator(registry)

    route = asyncio.run(orchestrator.route("600519.SH 要不要卖，回看15天"))

    assert route.skill is not None
    assert route.skill.metadata.id == "position_advisor"
    assert route.params == {
        "symbol": "600519.SH",
        "intent": "reduce",
        "lookback_days": 15,
    }
