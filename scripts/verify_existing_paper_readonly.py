"""Check existing StockManager paper accounts through an isolated Agent copy.

The source SQLite database is opened immutable and backed up to a temporary
directory. All services use the copy; model planning and provider calls are
disabled. No source account is advanced or changed.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
import uvicorn
from verify_agent_paper_engine import (
    STOCK_PYTHON,
    STOCK_SOURCE,
    free_local_port,
    wait_http,
    wait_task,
)

from tradingagents.api.app import create_app
from tradingagents.default_config import DEFAULT_CONFIG

SOURCE_DB = STOCK_SOURCE / "outputs/runtime_v2/sessions.db"
STOCK_SERVER = '''\
import os
from pathlib import Path
import uvicorn
from stockmanager.web.app import create_app

uvicorn.run(create_app(root=Path(os.environ["PAPER_COPY_ROOT"]), enable_live_services=False),
            host="127.0.0.1", port=int(os.environ["PAPER_COPY_PORT"]), log_level="error")
'''


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def backup_source(destination: Path) -> str:
    if not SOURCE_DB.is_file():
        raise RuntimeError(f"StockManager session database missing: {SOURCE_DB}")
    wal = Path(f"{SOURCE_DB}-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError("Source database has a live WAL; stop writes before immutable backup")
    source_hash = digest(SOURCE_DB)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"{SOURCE_DB.as_uri()}?mode=ro&immutable=1", uri=True) as source, \
            sqlite3.connect(destination) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Temporary database failed integrity check")
    if digest(SOURCE_DB) != source_hash:
        raise RuntimeError("Source database changed during backup; rerun against a stable snapshot")
    return source_hash


async def check_browser(trading_port: int, cases: list[dict]) -> None:
    frontend_root = Path(__file__).resolve().parents[1] / "frontend"
    frontend_port = free_local_port()
    child_env = {key: value for key, value in os.environ.items()
                 if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}}
    child_env["VITE_BACKEND_PROXY_TARGET"] = f"http://127.0.0.1:{trading_port}"
    vite = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port",
         str(frontend_port), "--strictPort"], cwd=frontend_root, env=child_env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        await wait_http(f"http://127.0.0.1:{frontend_port}/")
        browser_env = {**child_env,
                       "EXISTING_PAPER_URL": f"http://127.0.0.1:{frontend_port}",
                       "EXISTING_PAPER_CASES": json.dumps(cases)}
        browser = await asyncio.create_subprocess_exec(
            "node", str(frontend_root / "e2e/real-existing-paper-check.mjs"),
            cwd=frontend_root, env=browser_env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(browser.communicate(), timeout=90)
        except asyncio.TimeoutError:
            browser.kill()
            await browser.communicate()
            raise RuntimeError("Existing-account browser check timed out") from None
        if browser.returncode:
            raise RuntimeError(f"Existing-account browser check failed:\n{stderr.decode()[-4000:]}")
        print(stdout.decode().strip())
    finally:
        vite.terminate()
        try:
            _, stderr = vite.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            vite.kill()
            _, stderr = vite.communicate()
        if vite.returncode not in {0, -15}:
            print(stderr[-3000:], file=sys.stderr)


async def check_copied_lifecycle(stock_port: int, sessions: list[dict],
                                 stored_ids: list[str]) -> None:
    parent = next((row for row in sessions
                   if row.get("params", {}).get("kind") == "composite" and
                   row.get("params", {}).get("child_session_ids")), None)
    if parent is None:
        raise RuntimeError("No composite account in the temporary copy")
    parent_id = parent["session_id"]
    child_ids = parent["params"]["child_session_ids"]
    account_ids = [parent_id, *child_ids]
    other_id = next((value for value in stored_ids if value not in account_ids), None)
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{stock_port}", timeout=15) as stock:
        for child_id in child_ids:
            child_path = f"/api/v2/paper/{quote(child_id, safe='')}"
            assert (await stock.post(child_path + "/reset")).status_code == 409
            assert (await stock.delete(f"/api/v2/sessions/{quote(child_id, safe='')}")).status_code == 409
            assert (await stock.get(child_path + "/next_plan?force=true")).status_code == 409
        parent_path = f"/api/v2/paper/{quote(parent_id, safe='')}"
        reset = await stock.post(parent_path + "/reset")
        assert reset.status_code == 200, "Temporary composite reset failed"
        for account_id in account_ids:
            status = await stock.get(f"/api/v2/paper/{quote(account_id, safe='')}/status")
            assert status.status_code == 200
            data = status.json()["data"]
            assert data["session"]["last_date"] is None
            assert data.get("snapshot") is None
        if other_id:
            assert (await stock.get(f"/api/v2/paper/{quote(other_id, safe='')}/status")).status_code == 200
        deleted = await stock.delete(f"/api/v2/sessions/{quote(parent_id, safe='')}")
        assert deleted.status_code == 200, "Temporary composite delete failed"
        for account_id in account_ids:
            assert (await stock.get(f"/api/v2/paper/{quote(account_id, safe='')}/status")).status_code == 404
        if other_id:
            assert (await stock.get(f"/api/v2/paper/{quote(other_id, safe='')}/status")).status_code == 200
    print(json.dumps({"copied_group_accounts": len(account_ids),
                      "child_direct_mutations": "blocked",
                      "group_reset_delete": "atomic",
                      "source_writes": 0}))


async def main(*, with_browser: bool = False, with_lifecycle: bool = False) -> None:
    if not (STOCK_SOURCE / "stockmanager").is_dir() or not STOCK_PYTHON.is_file():
        raise RuntimeError(f"StockManager checkout and virtualenv required: {STOCK_SOURCE}")
    with tempfile.TemporaryDirectory(prefix="existing-paper-readonly-") as folder:
        temporary = Path(folder)
        stock_root = temporary / "stock"
        shutil.copytree(STOCK_SOURCE / "stockmanager", stock_root / "stockmanager",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env*"))
        copied_db = stock_root / "outputs/runtime_v2/sessions.db"
        source_hash = backup_source(copied_db)
        with sqlite3.connect(copied_db) as copied:
            stored_ids = [row[0] for row in copied.execute(
                "SELECT session_id FROM sessions WHERE mode='paper' ORDER BY updated_at DESC"
            )]
        stock_port, trading_port = free_local_port(), free_local_port()
        while trading_port == stock_port:
            trading_port = free_local_port()
        server_script = temporary / "stock_server.py"
        server_script.write_text(STOCK_SERVER)
        stock_env = {key: value for key, value in os.environ.items()
                     if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}}
        stock_env.update(PYTHONPATH=str(stock_root), PAPER_COPY_ROOT=str(stock_root),
                         PAPER_COPY_PORT=str(stock_port))
        stock = subprocess.Popen([str(STOCK_PYTHON), str(server_script)], cwd=stock_root,
                                 env=stock_env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        trading_server = None
        trading_task = None
        try:
            await wait_http(f"http://127.0.0.1:{stock_port}/api/health")
            os.environ["TRADINGAGENTS_APP_DB"] = str(temporary / "agent.db")
            DEFAULT_CONFIG.update(results_dir=str(temporary / "reports"),
                                  scheduler_enabled=False,
                                  stockmanager_mcp_enabled=False,
                                  ticker_name_backfill_enabled=False,
                                  agent_model_planning_enabled=False,
                                  llm_provider="offline-disabled",
                                  stockmanager_web_url=f"http://127.0.0.1:{stock_port}")
            app = create_app()
            trading_server = uvicorn.Server(uvicorn.Config(
                app, host="127.0.0.1", port=trading_port, log_level="error"))
            trading_task = asyncio.create_task(trading_server.serve())
            for _ in range(100):
                if trading_server.started:
                    break
                await asyncio.sleep(0.1)
            assert trading_server.started

            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{trading_port}",
                                         timeout=15) as ta:
                listed = await ta.get("/api/v1/paper/sessions")
                listed.raise_for_status()
                sessions = listed.json()
                if not sessions:
                    raise RuntimeError("Temporary copy has no paper sessions")
                assert {item["session_id"] for item in sessions} <= set(stored_ids)
                kinds: dict[str, dict] = {}
                observed: dict[str, dict] = {}
                for index, session_id in enumerate(stored_ids, start=1):
                    encoded = quote(session_id, safe="")
                    status_response = await ta.get(f"/api/v1/paper/sessions/{encoded}/status")
                    assert status_response.status_code == 200, f"paper session #{index}: status failed"
                    status = status_response.json()
                    assert status["session"]["session_id"] == session_id, index
                    created = await ta.post("/api/v1/agent/conversations", json={
                        "paper_session_id": session_id,
                    })
                    assert created.status_code == 201, f"paper session #{index}: binding failed"
                    conversation_id = created.json()["id"]
                    submitted = await ta.post(
                        f"/api/v1/agent/conversations/{conversation_id}/tasks",
                        json={"message": "总结这个模拟盘当前账本的已核对事实。"},
                    )
                    assert submitted.status_code == 202, f"paper session #{index}: task failed"
                    task = await wait_task(ta, submitted.json()["id"],
                                           {"completed", "failed", "needs_input"})
                    assert task["status"] == "completed", f"paper session #{index}: {task['status']}"
                    assert task["proposal"] is None, index
                    assert task["evidence"] and task["evidence"][0]["tool_name"] == "get_paper_session", index
                    evidence = task["evidence"][0]
                    assert not evidence["result"].get("error"), f"paper session #{index}: read failed"
                    assert evidence["result"]["session_id"] == session_id, index
                    snapshot = status.get("snapshot") or {}
                    assert evidence["as_of_date"] == (snapshot.get("as_of_date") or
                                                      status["session"].get("last_date")), index
                    kind = "composite" if status.get("kind") == "composite" else "single"
                    observed[session_id] = {"status": status,
                                            "browser": {"id": session_id,
                                                        "equity": snapshot.get("equity"),
                                                        "conversation": conversation_id}}
                    if snapshot.get("equity") is not None and kind not in kinds:
                        kinds[kind] = {"id": session_id, "equity": snapshot["equity"],
                                       "conversation": conversation_id}
                print(json.dumps({"stored_paper_sessions": len(stored_ids),
                                  "listed_paper_sessions": len(sessions),
                                  "read_only_agent_tasks": len(stored_ids),
                                  "sample_kinds": sorted(kinds)}, ensure_ascii=False))
                if with_browser:
                    linked = next((
                        [item["browser"], observed[child_id]["browser"]]
                        for item in observed.values()
                        if item["status"].get("kind") == "composite"
                        and item["browser"]["equity"] is not None
                        for child_id in (item["status"]["session"].get("params") or {}).get("child_session_ids", [])
                        if child_id in observed and observed[child_id]["browser"]["equity"] is not None
                    ), None)
                    if linked is None:
                        raise RuntimeError("No parent-child account pair with snapshots for browser check")
                    await check_browser(trading_port, linked)
                if with_lifecycle:
                    await check_copied_lifecycle(stock_port, sessions, stored_ids)
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
            if digest(SOURCE_DB) != source_hash:
                raise RuntimeError("Source database changed during read-only verification")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-browser", action="store_true",
                        help="Also verify a composite and one of its child accounts in a local browser")
    parser.add_argument("--with-lifecycle", action="store_true",
                        help="Reset and delete one composite account only in the temporary copy")
    args = parser.parse_args()
    asyncio.run(main(with_browser=args.with_browser, with_lifecycle=args.with_lifecycle))
