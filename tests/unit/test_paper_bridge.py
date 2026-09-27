"""Paper bridge contracts: fixed routes, paper-only writes, and safe upstream."""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.core.stockmanager_paper import PaperServiceError, paper_request
from tradingagents.default_config import DEFAULT_CONFIG


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "paper.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "ticker_name_backfill_enabled", False)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_paper_routes_use_stockmanager_ledger(client):
    async def fake_request(_config, method, path, payload=None):
        if path == "/api/v2/allocator-configs":
            return {"ok": True, "items": [{"path": "config/allocators/demo.json"}]}
        if path == "/api/v2/sessions?mode=paper":
            return {"ok": True, "items": [{"session_id": "paper:one"}]}
        if path == "/api/v2/sessions":
            assert method == "POST"
            assert payload == {
                "mode": "paper", "strategy": "demo", "config": "", "initial_cash": 1000000,
                "start_date": "2026-01-02", "warmup_days": 760, "suffix": "default",
            }
            return {"ok": True, "session_id": "paper:one"}
        if path == "/api/v2/paper/paper:one/advance":
            assert payload in (
                {"target_date": "2026-01-05"},
                {"target_date": "2026-01-05", "expected_state_fingerprint": "a" * 64},
            )
            return {"ok": True, "job_id": "job-1"}
        if path == "/api/jobs/job-1":
            return {"job_id": "job-1", "state": "success", "progress": 100, "message": "done", "result": None}
        raise AssertionError(path)

    with patch("tradingagents.api.routes.paper.paper_request", fake_request):
        assert client.get("/api/v1/paper/allocator-configs").json()[0]["path"] == "config/allocators/demo.json"
        assert client.get("/api/v1/paper/sessions").json() == [{"session_id": "paper:one"}]
        created = client.post("/api/v1/paper/sessions", json={"strategy": "demo", "start_date": "2026-01-02"})
        assert created.json()["session_id"] == "paper:one"
        assert client.post("/api/v1/paper/sessions", json={"strategy": "demo"}).status_code == 422
        assert client.post("/api/v1/paper/sessions", json={"allocator_config_path": "../../secrets.json"}).status_code == 422
        assert client.post("/api/v1/paper/sessions/paper:one/advance", json={"target_date": "2026-01-05"}).json()["job_id"] == "job-1"
        assert client.post("/api/v1/paper/sessions/paper:one/advance", json={
            "target_date": "2026-01-05", "expected_state_fingerprint": "a" * 64,
        }).json()["job_id"] == "job-1"
        assert client.post("/api/v1/paper/sessions/paper:one/advance", json={
            "target_date": "2026-01-05", "expected_state_fingerprint": "invalid",
        }).status_code == 422
        assert client.get("/api/v1/paper/jobs/job-1").json()["state"] == "success"
        assert client.get("/api/v1/paper/sessions/%2Fetc/status").status_code in (400, 404)


def test_paper_client_rejects_remote_hosts_and_redirects():
    with pytest.raises(PaperServiceError, match="本机"):
        asyncio.run(paper_request({"stockmanager_web_url": "http://example.com"}, "GET", "/api/v2/sessions"))

    class Redirect:
        ok = False
        status_code = 302

        @staticmethod
        def json():
            return {"detail": "redirect"}

    with patch("tradingagents.core.stockmanager_paper.requests.request", return_value=Redirect()) as send:
        with pytest.raises(PaperServiceError):
            asyncio.run(paper_request({"stockmanager_web_url": "http://127.0.0.1:8787"}, "GET", "/api/v2/sessions"))
        assert send.call_args.kwargs["allow_redirects"] is False
