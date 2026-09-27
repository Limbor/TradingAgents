"""Check the Agent paper approval path against isolated localhost services.

Copies only StockManager source and config to a temporary directory, creates a
synthetic ledger, and substitutes a deterministic paper runner. No real account
data, market calls, model calls, or repository files are modified.

Run with the TradingAgents API virtualenv Python. Set STOCKMANAGER_ROOT when the
StockManager checkout is not a sibling of this repository.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import uvicorn

from tradingagents.api.app import create_app
from tradingagents.default_config import DEFAULT_CONFIG

STOCK_SOURCE = Path(os.environ.get(
    "STOCKMANAGER_ROOT", str(Path(__file__).resolve().parents[2] / "stockmanager")
)).resolve()
STOCK_PYTHON = STOCK_SOURCE / ".venv/bin/python"

STOCK_SERVER = '''\
import os
from pathlib import Path
import uvicorn
from stockmanager.web.app import create_app
from stockmanager.runtime_v2.paper_runner import PaperAdvanceResult, PaperRunner
from stockmanager.runtime_v2.session_store import SessionStore

root = Path(os.environ["SYNTH_STOCK_ROOT"])
store = SessionStore(root / "outputs/runtime_v2/sessions.db")
session_id = "paper:synthetic"
store.upsert_session(session_id=session_id, mode="paper", strategy="synthetic",
    config_name="", strategy_hash="synthetic", config_hash="",
    initial_cash=100000, params={"start_date": "2026-09-24"})
store.set_last_date(session_id, "2026-09-24")
store.put_snapshot(session_id, as_of_date="2026-09-24", equity=100000,
    cash=100000, positions={}, engine_state={})
store.put_next_plan(session_id, {"signal_date": "2026-09-24", "items": []})
calls = []

def synthetic_advance(self, session_id, *, target_date, skip_next_plan=False,
                      expected_state_fingerprint=None):
    calls.append((session_id, target_date))
    assert session_id == "paper:synthetic"
    assert isinstance(expected_state_fingerprint, str) and len(expected_state_fingerprint) == 64
    self.store.require_paper_state_fingerprint(session_id, expected_state_fingerprint)
    if target_date == "2026-09-26":
        assert self.store.get_session(session_id).last_date == "2026-09-25"
        return PaperAdvanceResult(session_id=session_id, advanced_days=0,
            last_date="2026-09-25", equity=101000, cash=101000,
            positions_count=0, new_trades=0)
    assert target_date == "2026-09-25"
    self.store.commit_paper_advance(session_id, expected_last_date="2026-09-24",
        expected_state_fingerprint=expected_state_fingerprint,
        as_of_date=target_date, equity=101000, cash=101000,
        positions={}, engine_state={})
    self.store.put_next_plan(session_id, {"signal_date": target_date, "items": []})
    return PaperAdvanceResult(session_id=session_id, advanced_days=1,
        last_date=target_date, equity=101000, cash=101000,
        positions_count=0, new_trades=0)

PaperRunner.advance_to = synthetic_advance
app = create_app(root=root, enable_live_services=False)
app.add_api_route("/__test/advance_calls", lambda: {"calls": calls})

def mutate_cash_without_new_day():
    store.put_snapshot(session_id, as_of_date="2026-09-25", equity=101000,
        cash=100500, positions={}, engine_state={})
    return {"cash": 100500}

app.add_api_route("/__test/mutate_cash", mutate_cash_without_new_day, methods=["POST"])
uvicorn.run(app, host="127.0.0.1", port=int(os.environ["SYNTH_STOCK_PORT"]), log_level="error")
'''


def free_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def wait_http(url: str) -> None:
    async with httpx.AsyncClient(timeout=2) as client:
        for _ in range(100):
            try:
                if (await client.get(url)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError(f"server unavailable: {url}")


async def main() -> None:
    if not (STOCK_SOURCE / "stockmanager").is_dir() or not STOCK_PYTHON.is_file():
        raise RuntimeError(f"StockManager source and virtualenv not found: {STOCK_SOURCE}")
    with tempfile.TemporaryDirectory(prefix="agent-paper-live-") as folder:
        temporary = Path(folder)
        original_cwd = Path.cwd()
        os.chdir(temporary)
        stock_port, trading_port = free_local_port(), free_local_port()
        while trading_port == stock_port:
            trading_port = free_local_port()
        stock_root = temporary / "stock"
        shutil.copytree(
            STOCK_SOURCE / "stockmanager", stock_root / "stockmanager",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env*"),
        )
        (stock_root / "config").mkdir()
        script = temporary / "stock_server.py"
        script.write_text(STOCK_SERVER)
        child_env = {key: value for key, value in os.environ.items()
                     if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}}
        child_env["PYTHONPATH"] = str(stock_root)
        child_env["SYNTH_STOCK_ROOT"] = str(stock_root)
        child_env["SYNTH_STOCK_PORT"] = str(stock_port)
        child_env.pop("STOCKMANAGER_API_KEY", None)
        stock = subprocess.Popen([str(STOCK_PYTHON), str(script)],
                                 cwd=stock_root, env=child_env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        trading_server = None
        trading_task = None
        try:
            await wait_http(f"http://127.0.0.1:{stock_port}/api/health")
            os.environ["TRADINGAGENTS_APP_DB"] = str(temporary / "tradingagents.db")
            DEFAULT_CONFIG["results_dir"] = str(temporary / "reports")
            DEFAULT_CONFIG["scheduler_enabled"] = False
            DEFAULT_CONFIG["stockmanager_mcp_enabled"] = False
            DEFAULT_CONFIG["ticker_name_backfill_enabled"] = False
            DEFAULT_CONFIG["agent_model_planning_enabled"] = False
            DEFAULT_CONFIG["stockmanager_web_url"] = f"http://127.0.0.1:{stock_port}"
            app = create_app()
            trading_server = uvicorn.Server(uvicorn.Config(
                app, host="127.0.0.1", port=trading_port, log_level="error"))
            trading_task = asyncio.create_task(trading_server.serve())
            for _ in range(100):
                if trading_server.started:
                    break
                await asyncio.sleep(0.1)
            assert trading_server.started

            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{trading_port}", timeout=10) as ta, \
                    httpx.AsyncClient(base_url=f"http://127.0.0.1:{stock_port}", timeout=10) as sm:
                created = await ta.post("/api/v1/agent/conversations", json={
                    "paper_session_id": "paper:synthetic"})
                assert created.status_code == 201, created.text
                cid = created.json()["id"]
                submitted = await ta.post(f"/api/v1/agent/conversations/{cid}/tasks", json={
                    "message": "推进模拟盘到 2026-09-25"})
                assert submitted.status_code == 202, submitted.text
                task_id = submitted.json()["id"]
                task = None
                for _ in range(100):
                    task = (await ta.get(f"/api/v1/agent/tasks/{task_id}")).json()
                    if task["status"] == "awaiting_approval":
                        break
                    await asyncio.sleep(0.1)
                assert task and task["status"] == "awaiting_approval", task
                before = (await sm.get("/api/v2/paper/paper:synthetic/status")).json()["data"]
                assert before["session"]["last_date"] == "2026-09-24"
                assert (await sm.get("/__test/advance_calls")).json()["calls"] == []
                proposal_id = task["proposal"]["id"]
                first = await ta.post(f"/api/v1/agent/proposals/{proposal_id}/approve")
                second = await ta.post(f"/api/v1/agent/proposals/{proposal_id}/approve")
                assert first.status_code == second.status_code == 200
                for _ in range(100):
                    task = (await ta.get(f"/api/v1/agent/tasks/{task_id}")).json()
                    if task["status"] in {"completed", "failed", "needs_review"}:
                        break
                    await asyncio.sleep(0.1)
                assert task["status"] == "completed", task
                after = (await sm.get("/api/v2/paper/paper:synthetic/status")).json()["data"]
                calls = (await sm.get("/__test/advance_calls")).json()["calls"]
                assert after["session"]["last_date"] == "2026-09-25"
                assert after["snapshot"]["equity"] == 101000
                assert calls == [["paper:synthetic", "2026-09-25"]], calls
                assert task["proposal"]["status"] == "completed"
                events = [item["event_type"] for item in task["events"]]
                assert "proposal_created" in events and "action_submitted" in events
                assert "action_completed" in events
                no_day = await ta.post(f"/api/v1/agent/conversations/{cid}/tasks", json={
                    "message": "推进模拟盘到 2026-09-26"})
                assert no_day.status_code == 202, no_day.text
                no_day_id = no_day.json()["id"]
                no_day_task = None
                for _ in range(100):
                    no_day_task = (await ta.get(f"/api/v1/agent/tasks/{no_day_id}")).json()
                    if no_day_task["status"] == "awaiting_approval":
                        break
                    await asyncio.sleep(0.1)
                assert no_day_task and no_day_task["status"] == "awaiting_approval", no_day_task
                no_day_proposal = no_day_task["proposal"]["id"]
                assert (await ta.post(f"/api/v1/agent/proposals/{no_day_proposal}/approve")).status_code == 200
                assert (await ta.post(f"/api/v1/agent/proposals/{no_day_proposal}/approve")).status_code == 200
                for _ in range(100):
                    no_day_task = (await ta.get(f"/api/v1/agent/tasks/{no_day_id}")).json()
                    if no_day_task["status"] in {"completed", "failed", "needs_review"}:
                        break
                    await asyncio.sleep(0.1)
                assert no_day_task["status"] == "completed", no_day_task
                assert no_day_task["proposal"]["status"] == "no_change", no_day_task
                assert "账本未推进" in no_day_task["result"]["content"]
                assert "action_no_change" in [event["event_type"] for event in no_day_task["events"]]
                no_day_ledger = (await sm.get("/api/v2/paper/paper:synthetic/status")).json()["data"]
                assert no_day_ledger["session"]["last_date"] == "2026-09-25"
                calls = (await sm.get("/__test/advance_calls")).json()["calls"]
                assert calls == [["paper:synthetic", "2026-09-25"],
                                 ["paper:synthetic", "2026-09-26"]], calls
                stale = await ta.post(f"/api/v1/agent/conversations/{cid}/tasks", json={
                    "message": "推进模拟盘到 2026-09-29"})
                assert stale.status_code == 202, stale.text
                stale_id = stale.json()["id"]
                stale_task = None
                for _ in range(100):
                    stale_task = (await ta.get(f"/api/v1/agent/tasks/{stale_id}")).json()
                    if stale_task["status"] == "awaiting_approval":
                        break
                    await asyncio.sleep(0.1)
                assert stale_task and stale_task["status"] == "awaiting_approval", stale_task
                assert stale_task["proposal"]["baseline"].get("state_fingerprint")
                assert (await sm.post("/__test/mutate_cash")).status_code == 200
                stale_proposal = stale_task["proposal"]["id"]
                assert (await ta.post(f"/api/v1/agent/proposals/{stale_proposal}/approve")).status_code == 200
                for _ in range(100):
                    stale_task = (await ta.get(f"/api/v1/agent/tasks/{stale_id}")).json()
                    if stale_task["status"] in {"completed", "failed", "needs_review"}:
                        break
                    await asyncio.sleep(0.1)
                assert stale_task["proposal"]["status"] == "stale", stale_task
                assert stale_task["proposal"]["result"]["reason"] == "account_state_changed"
                assert (await sm.get("/__test/advance_calls")).json()["calls"] == calls
                print(json.dumps({"status": task["status"], "no_day_status": no_day_task["proposal"]["status"],
                                  "same_day_change_status": stale_task["proposal"]["status"],
                                  "advance_calls": len(calls),
                                  "before": before["session"]["last_date"],
                                  "after": after["session"]["last_date"],
                                  "events": events}, ensure_ascii=False))
        finally:
            if trading_server and trading_task:
                trading_server.should_exit = True
                await trading_task
            stock.terminate()
            try:
                _, stderr = stock.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stock.kill()
                _, stderr = stock.communicate()
            if stock.returncode not in {0, -15}:
                print(stderr[-3000:], file=sys.stderr)
            os.chdir(original_cwd)


asyncio.run(main())
