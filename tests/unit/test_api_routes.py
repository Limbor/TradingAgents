"""Integration tests for API routes using FastAPI TestClient."""

import pytest
from fastapi.testclient import TestClient

from tradingagents.api.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "api-routes.db"))
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "tradingagents-api"}


def test_list_skills(client):
    res = client.get("/api/v1/skills")
    assert res.status_code == 200
    skills = res.json()
    assert len(skills) >= 1
    assert "stock_analysis" in {skill["id"] for skill in skills}


def test_get_skill_schema(client):
    res = client.get("/api/v1/skills/stock_analysis/schema")
    assert res.status_code == 200
    schema = res.json()
    assert "properties" in schema
    assert "ticker" in schema["properties"]


def test_get_skill_schema_not_found(client):
    res = client.get("/api/v1/skills/nonexistent/schema")
    assert res.status_code == 404


def test_list_runs(client):
    res = client.get("/api/v1/runs")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_get_config(client):
    res = client.get("/api/v1/config")
    assert res.status_code == 200
    config = res.json()
    assert "llm_provider" in config
    assert "deep_think_llm" in config


def test_update_config(client):
    res = client.put(
        "/api/v1/config",
        json={"max_debate_rounds": 3},
    )
    assert res.status_code == 200
    assert res.json()["max_debate_rounds"] == 3


def test_list_reports(client):
    res = client.get("/api/v1/reports")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_get_report_not_found(client):
    res = client.get("/api/v1/reports/nonexistent")
    assert res.status_code == 404


def test_get_run_not_found(client):
    res = client.get("/api/v1/runs/nonexistent")
    assert res.status_code == 404


def test_create_run_invalid_skill(client):
    res = client.post(
        "/api/v1/runs",
        json={"skill_id": "nonexistent", "params": {}},
    )
    assert res.status_code == 404
