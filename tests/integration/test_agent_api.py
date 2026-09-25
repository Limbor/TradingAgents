"""Trading Agent REST contract from conversation creation to persisted result."""

import time

from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.default_config import DEFAULT_CONFIG


def test_agent_task_api_persists_evidence_and_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "agent-api.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "ticker_name_backfill_enabled", False)
    app = create_app()
    with TestClient(app) as client:
        async def paper_handler(session_id):
            assert session_id == "paper:api"
            return {"session_id": session_id, "source": "StockManager ledger",
                    "as_of_date": "2026-09-25", "snapshot": {
                        "equity": 200000, "cash": 100000, "positions": {}},
                    "recent_trades": []}

        async def synthesize(*_args):
            return "模拟盘权益 20 万元；来源为 StockManager 账本。"

        app.state.tool_registry.get("get_paper_session").handler = paper_handler
        app.state.agent_harness._synthesize = synthesize
        response = client.post("/api/v1/agent/conversations", json={
            "title": "模拟盘", "paper_session_id": "paper:api"})
        assert response.status_code == 201
        cid = response.json()["id"]

        response = client.post(f"/api/v1/agent/conversations/{cid}/tasks",
                               json={"message": "评估当前账户"})
        assert response.status_code == 202
        task_id = response.json()["id"]

        for _ in range(100):
            detail = client.get(f"/api/v1/agent/conversations/{cid}").json()
            if detail["tasks"][0]["status"] == "completed":
                break
            time.sleep(0.01)
        assert detail["paper_session_id"] == "paper:api"
        assert detail["tasks"][0]["status"] == "completed"
        assert detail["tasks"][0]["evidence"][0]["source"] == "StockManager ledger"
        assert detail["messages"][-1]["role"] == "assistant"
        events = client.get(f"/api/v1/agent/tasks/{task_id}/events?after_seq=1").json()
        assert events[0]["event_type"] == "plan_created"


def test_agent_api_rejects_invalid_scope_and_empty_message(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "agent-invalid.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "ticker_name_backfill_enabled", False)
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/agent/conversations", json={
            "paper_session_id": "../private"}).status_code == 422
        cid = client.post("/api/v1/agent/conversations", json={}).json()["id"]
        assert client.post(f"/api/v1/agent/conversations/{cid}/tasks",
                           json={"message": ""}).status_code == 422
        assert client.get("/api/v1/agent/conversations/missing").status_code == 404


def test_agent_proposal_api_requires_confirm_before_paper_write(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "agent-proposal.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "ticker_name_backfill_enabled", False)
    writes = []

    async def tool(session_id):
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25", "snapshot": {"equity": 100000, "positions": {}}}

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            writes.append(payload)
            return {"job_id": "job:api"}
        if path.endswith("/status"):
            as_of = "2026-09-28" if writes else "2026-09-25"
            return {"data": {"snapshot": {"as_of_date": as_of, "equity": 101000}}}
        return {"state": "success", "result": {"data": {
            "session_id": "paper:api", "last_date": "2026-09-28", "advanced_days": 1}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    app = create_app()
    with TestClient(app) as client:
        app.state.tool_registry.get("get_paper_session").handler = tool
        cid = client.post("/api/v1/agent/conversations", json={
            "paper_session_id": "paper:api"}).json()["id"]
        tid = client.post(f"/api/v1/agent/conversations/{cid}/tasks", json={
            "message": "推进模拟盘到 2026-09-28"}).json()["id"]
        for _ in range(100):
            task = client.get(f"/api/v1/agent/tasks/{tid}").json()
            if task["status"] == "awaiting_approval":
                break
            time.sleep(0.01)
        assert task["status"] == "awaiting_approval"
        assert writes == []
        pid = task["proposal"]["id"]
        assert client.post(f"/api/v1/agent/proposals/{pid}/approve").status_code == 200
        assert client.post(f"/api/v1/agent/proposals/{pid}/approve").status_code == 200
        for _ in range(100):
            task = client.get(f"/api/v1/agent/tasks/{tid}").json()
            if task["status"] == "completed":
                break
            time.sleep(0.01)
        assert task["status"] == "completed"
        assert writes == [{"target_date": "2026-09-28"}]
        checked = client.post(f"/api/v1/agent/proposals/{pid}/reconcile")
        assert checked.status_code == 200
        assert checked.json()["status"] == "completed"
        assert writes == [{"target_date": "2026-09-28"}]
        assert client.post("/api/v1/agent/proposals/missing/reconcile").status_code == 404
