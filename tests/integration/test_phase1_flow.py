"""Phase 1 end-to-end API and WebSocket flow tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi.testclient import TestClient
from pydantic import BaseModel

from tradingagents.api.app import create_app
from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


class MockReportInput(BaseModel):
    ticker: str = "MOCK"


class MockReportOutput(BaseModel):
    status: str


class MockReportSkill(BaseSkill):
    @property
    def metadata(self):
        return SkillMetadata(
            id="mock_report",
            name="Mock Report",
            description="Fast deterministic report skill for Phase 1 flow tests",
            version="0.1.0",
            triggers=["mock"],
        )

    @property
    def input_schema(self):
        return MockReportInput

    @property
    def output_schema(self):
        return MockReportOutput

    async def execute(self, params, config) -> AsyncIterator[SkillEvent]:
        run_id = config["run_id"]
        db = config["db"]
        sections = {
            "market_report": f"{params.ticker} tape is constructive.",
            "final_trade_decision": "**Rating**: Buy\n\nRisk-managed long bias.",
        }

        yield SkillEvent(
            event_type="agent_status",
            data={"agent": "Market Analyst", "status": "running"},
        )
        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "market_report",
                "content": sections["market_report"],
                "is_final": False,
            },
        )
        db.save_report(
            report_id=f"report-{run_id}",
            run_id=run_id,
            ticker=params.ticker,
            rating="Buy",
            content="\n\n".join(sections.values()),
            path=None,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={"status": "success", "ticker": params.ticker},
        )

    async def cancel(self) -> None:
        return None


def test_phase1_create_run_stream_and_report_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "phase1.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    app = create_app()
    with TestClient(app) as client:
        db = Database(tmp_path / "phase1.db")
        client.app.state.db = db
        client.app.state.run_manager = RunManager(db=db)
        client.app.state.registry.register(MockReportSkill())

        response = client.post(
            "/api/v1/runs",
            json={"skill_id": "mock_report", "params": {"ticker": "MOCK"}},
        )
        assert response.status_code == 200
        run_id = response.json()["id"]

        messages = []
        with client.websocket_connect(f"/ws/run/{run_id}") as websocket:
            while True:
                message = websocket.receive_json()
                messages.append(message)
                if message["type"] == "run_complete":
                    break

        event_types = [message["type"] for message in messages]
        assert "agent_status" in event_types
        assert "report_chunk" in event_types
        assert "skill_complete" in event_types
        assert event_types[-1] == "run_complete"

        stored_run = db.get_run(run_id)
        assert stored_run is not None
        assert stored_run["status"] == "completed"

        reports = db.list_reports(ticker="MOCK")
        assert len(reports) == 1
        assert reports[0]["run_id"] == run_id

        reports_response = client.get("/api/v1/reports?ticker=MOCK")
        assert reports_response.status_code == 200
        assert reports_response.json()[0]["run_id"] == run_id
