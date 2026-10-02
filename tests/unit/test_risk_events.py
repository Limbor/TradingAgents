"""Structured risk-event persistence and API tests."""

from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.core.persistence import Database
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.skills.risk_monitor.skill import _persist_risk_events


def test_risk_event_dedup_and_lifecycle(tmp_path):
    db = Database(tmp_path / "risk-events.db")
    risks = [
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "level": "red",
            "message": "critical",
            "announcements": [
                {"title": "公司被立案调查", "date": "2026-07-10", "source": "exchange"}
            ],
        }
    ]
    _persist_risk_events(db, risks)
    first = db.list_risk_events()
    _persist_risk_events(db, risks)
    second = db.list_risk_events()

    assert len(first) == len(second) == 1
    assert risks[0]["risk_event_ids"] == [first[0]["id"]]
    acknowledged = db.update_risk_event_status(first[0]["id"], "acknowledged")
    assert acknowledged["status"] == "acknowledged"
    resolved = db.update_risk_event_status(first[0]["id"], "resolved")
    assert resolved["resolved_at"] is not None


def test_risk_event_api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "risk-api.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    app = create_app()
    with TestClient(app) as client:
        db = client.app.state.db
        item = db.upsert_risk_event(
            event_id="risk:test",
            symbol="600519.SH",
            name="贵州茅台",
            level="orange",
            title="股东减持公告",
        )
        response = client.get("/api/v1/risk-events")
        assert response.status_code == 200
        assert response.json()[0]["id"] == item["id"]

        response = client.patch(
            "/api/v1/risk-events/risk:test", json={"status": "monitoring"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "monitoring"
