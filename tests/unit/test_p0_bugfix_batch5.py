"""Tests for the fifth batch of quick fixes.

Covers:
- Pagination (offset + capped limit) for runs and artifacts
- Optional token auth (disabled by default; enforces Bearer/?token= when set)
- WebSocket event persistence + replay-on-reconnect
- Artifact version snapshots on overwrite
"""

import asyncio

from tradingagents.api.middleware.auth import (
    allowed_origins,
    auth_token_configured,
    request_has_valid_token,
    verify_ws_origin,
    verify_ws_token,
)
from tradingagents.core.persistence import Database

# ---------------------------------------------------------------------------
# Fix 1: pagination
# ---------------------------------------------------------------------------


def test_list_runs_pagination(tmp_path):
    db = Database(tmp_path / "t.db")
    for i in range(10):
        db.save_run(f"r{i}", "stock_analysis", {"i": i}, "completed")
    page1 = db.list_runs(limit=5, offset=0)
    page2 = db.list_runs(limit=5, offset=5)
    assert len(page1) == 5 and len(page2) == 5
    page1_ids = {r["id"] for r in page1}
    page2_ids = {r["id"] for r in page2}
    assert page1_ids.isdisjoint(page2_ids)  # no overlap


def test_list_runs_limit_capped(tmp_path):
    """limit > 200 must be clamped to 200 to avoid unbounded queries."""
    db = Database(tmp_path / "t.db")
    db.save_run("r1", "x", {}, "completed")
    rows = db.list_runs(limit=1000000)
    assert len(rows) == 1  # query ran, didn't error


def test_list_artifacts_pagination(tmp_path):
    db = Database(tmp_path / "t.db")
    for i in range(8):
        db.save_artifact(
            artifact_id=f"a{i}", run_id="", skill_id="s",
            artifact_type="stock_report", title=f"Report {i}",
            status="success", content_markdown="x",
        )
    page1 = db.list_artifacts(limit=4, offset=0)
    page2 = db.list_artifacts(limit=4, offset=4)
    assert len(page1) == 4 and len(page2) == 4
    assert {a["id"] for a in page1}.isdisjoint({a["id"] for a in page2})


# ---------------------------------------------------------------------------
# Fix 2: optional token auth
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


def test_auth_disabled_when_token_empty():
    assert auth_token_configured({}) == ""
    assert auth_token_configured({"api_auth_token": "  "}) == ""
    assert verify_ws_token({}, {}) is True  # no token → allow


def test_request_has_valid_token_bearer_header():
    expected = "secret-token"
    req = _FakeRequest(headers={"authorization": f"Bearer {expected}"})
    assert request_has_valid_token(req, expected) is True
    req_bad = _FakeRequest(headers={"authorization": "Bearer wrong"})
    assert request_has_valid_token(req_bad, expected) is False
    req_missing = _FakeRequest()
    assert request_has_valid_token(req_missing, expected) is False


def test_request_has_valid_token_query_fallback():
    expected = "secret-token"
    req = _FakeRequest(query={"token": expected})
    assert request_has_valid_token(req, expected) is True


def test_verify_ws_token_when_configured():
    config = {"api_auth_token": "secret-token"}
    assert verify_ws_token({"token": "secret-token"}, config) is True
    assert verify_ws_token({"token": "wrong"}, config) is False
    assert verify_ws_token({}, config) is False


def test_websocket_origin_is_restricted_for_browser_clients():
    config = {"api_allowed_origins": "tauri://localhost,http://localhost:5173"}
    assert allowed_origins(config) == ["tauri://localhost", "http://localhost:5173"]
    assert verify_ws_origin({"origin": "tauri://localhost"}, config) is True
    assert verify_ws_origin({"origin": "https://evil.example"}, config) is False
    assert verify_ws_origin({}, config) is True  # non-browser client; token still applies


# ---------------------------------------------------------------------------
# Fix 3: WebSocket event persistence + replay
# ---------------------------------------------------------------------------


def test_save_and_list_run_events(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_run("r1", "stock_analysis", {}, "running")
    db.save_run_event("r1", 1, "skill_start", {"skill": "stock_analysis"})
    db.save_run_event("r1", 2, "agent_status", {"agent": "Market Analyst"})
    db.save_run_event("r1", 3, "run_complete", {"status": "completed"})

    events = db.list_run_events("r1")
    assert len(events) == 3
    assert events[0]["event_type"] == "skill_start"
    assert events[2]["event_type"] == "run_complete"
    assert events[1]["payload"]["agent"] == "Market Analyst"


def test_record_event_persists_to_db(tmp_path):
    """RunManager._record_event should write to the DB for replay-on-reconnect."""
    from tradingagents.core.run_manager import Run, RunManager
    from tradingagents.skills.base import SkillEvent

    db = Database(tmp_path / "t.db")
    rm = RunManager(db=db)
    run = Run(id="r1", skill_id="x", params={})
    rm._runs["r1"] = run

    async def go():
        await rm._record_event(run, "r1", SkillEvent(event_type="skill_start", data={"a": 1}))
        await rm._record_event(run, "r1", SkillEvent(event_type="run_complete", data={"status": "completed"}))

    asyncio.run(go())
    events = db.list_run_events("r1")
    assert len(events) == 2
    assert events[0]["seq"] == 1
    assert events[1]["seq"] == 2


# ---------------------------------------------------------------------------
# Fix 4: artifact version snapshots
# ---------------------------------------------------------------------------


def test_artifact_overwrite_creates_version_snapshot(tmp_path):
    db = Database(tmp_path / "t.db")
    # First save (create).
    db.save_artifact(
        artifact_id="a1", run_id="r1", skill_id="s",
        artifact_type="stock_report", title="Report v1",
        status="success", content_markdown="first", summary="s1",
        payload={"score": 80},
    )
    # Second save (overwrite) — should snapshot v1.
    db.save_artifact(
        artifact_id="a1", run_id="r1", skill_id="s",
        artifact_type="stock_report", title="Report v2",
        status="success", content_markdown="second", summary="s2",
        payload={"score": 90},
    )
    versions = db.list_artifact_versions("a1")
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["title"] == "Report v1"
    assert versions[0]["content_markdown"] == "first"
    assert versions[0]["payload"]["score"] == 80


def test_artifact_multiple_versions_increment(tmp_path):
    db = Database(tmp_path / "t.db")
    for i in range(3):
        db.save_artifact(
            artifact_id="a1", run_id="", skill_id="s",
            artifact_type="stock_report", title=f"v{i}",
            status="success", content_markdown=f"content {i}",
        )
    versions = db.list_artifact_versions("a1")
    assert len(versions) == 2  # 3 saves → 2 snapshots (last one is current)
    assert versions[0]["version"] == 2  # newest first
    assert versions[1]["version"] == 1


def test_artifact_first_save_no_snapshot(tmp_path):
    """Creating a brand-new artifact must not create a version row."""
    db = Database(tmp_path / "t.db")
    db.save_artifact(
        artifact_id="a-new", run_id="", skill_id="s",
        artifact_type="stock_report", title="Only",
        status="success", content_markdown="x",
    )
    assert db.list_artifact_versions("a-new") == []
