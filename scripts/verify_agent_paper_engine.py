"""Exercise Agent paper approval with StockManager's real strategy engine.

All market bars, accounts, databases and service roots are synthetic and live
in one temporary directory. No vendor token or LLM call is needed.
"""

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
import uvicorn

from tradingagents.api.app import create_app
from tradingagents.default_config import DEFAULT_CONFIG

STOCK_SOURCE = Path(os.environ.get(
    "STOCKMANAGER_ROOT", str(Path(__file__).resolve().parents[2] / "StockManager")
)).resolve()
STOCK_PYTHON = STOCK_SOURCE / ".venv/bin/python"
CONFIG_NAME = "sz399101_mainboard_small_lowvol_top5_r60_exp60_100k"

STOCK_SERVER = '''\
import os
from pathlib import Path

import numpy as np
import pandas as pd
import uvicorn

from stockmanager.backtest_v2.data_tushare import SecurityInfo, TushareDataBundle, save_bundle
from stockmanager.runtime_v2.paper_runner import PaperRunner
from stockmanager.runtime_v2.session_store import SessionStore
from stockmanager.runtime_v2.strategy_registry import StrategyRegistry
from stockmanager.web.app import create_app

root = Path(os.environ["SYNTH_STOCK_ROOT"])
dates = pd.bdate_range("2025-01-02", periods=260)
codes = ["000001.SZ", "000002.SZ", "002001.SZ", "600000.SH", "600001.SH", "600036.SH"]
prices = {}
for number, code in enumerate(codes):
    ticks = np.arange(len(dates), dtype=float)
    close = 10.0 + number * 3.0 + ticks * (0.012 + number * 0.002) + np.sin(ticks / (5 + number)) * 0.2
    prices[code] = pd.DataFrame({
        "open": close * 0.998, "high": close * 1.01,
        "low": close * 0.99, "close": close, "volume": 10_000_000.0,
    }, index=dates)
index = pd.DataFrame({
    "open": 100.0, "high": 101.0, "low": 99.0,
    "close": 100.0 + np.arange(len(dates)) * 0.1, "volume": 10_000_000.0,
}, index=dates)
bundle = TushareDataBundle(
    universe_codes=codes, price_panels=prices,
    raw_price_panels={code: frame.copy() for code, frame in prices.items()},
    index_panels={"399101.SZ": index, "000906.SH": index.copy()},
    security_info={
        code: SecurityInfo(code=code, name=code,
                           start_date=pd.Timestamp("2010-01-01"), end_date=None)
        for code in codes
    },
    industry_map={code: "TEST" for code in codes}, trading_calendar=dates,
    index_members={"399101.SZ": {dates[0]: codes}, "000906.SH": {dates[0]: codes}},
    circ_mv_panel=pd.DataFrame({code: 1_000_000.0 + number * 100_000.0
                                for number, code in enumerate(codes)}, index=dates),
    corporate_actions_complete=True,
)
bundle_path = root / "synthetic_bundle.pkl"
save_bundle(bundle, str(bundle_path))
store = SessionStore(root / "outputs/runtime_v2/sessions.db")
runner = PaperRunner(store=store, registry=StrategyRegistry(root),
                     cache_dir=str(root / "data/cache"))
baseline = dates[120].date().isoformat()
target = dates[130].date().isoformat()
session = runner.create_or_get(
    strategy_name="sz399101_mainboard_small_lowvol",
    config_name="sz399101_mainboard_small_lowvol_top5_r60_exp60_100k",
    initial_cash=100000, start_date=baseline,
    warmup_days=120, bundle_cache=str(bundle_path),
)
seed = runner.advance_to(session.session_id, target_date=baseline)
assert seed.advanced_days == 1
app = create_app(root=root, enable_live_services=False)
app.add_api_route("/__test/context", lambda: {
    "session_id": session.session_id, "baseline": baseline, "target": target,
    "seed_trades": seed.new_trades,
})
uvicorn.run(app, host="127.0.0.1", port=int(os.environ["SYNTH_STOCK_PORT"]), log_level="error")
'''


def free_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def wait_http(url: str) -> None:
    async with httpx.AsyncClient(timeout=2) as client:
        for _ in range(200):
            try:
                if (await client.get(url)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError(f"service unavailable: {url}")


async def wait_task(client: httpx.AsyncClient, task_id: str, statuses: set[str]) -> dict:
    for _ in range(200):
        response = await client.get(f"/api/v1/agent/tasks/{task_id}")
        response.raise_for_status()
        task = response.json()
        if task["status"] in statuses:
            return task
        await asyncio.sleep(0.2)
    raise RuntimeError(f"Agent task {task_id} did not reach {statuses}")


async def main(*, with_model: bool = False) -> None:
    if not (STOCK_SOURCE / "stockmanager").is_dir() or not STOCK_PYTHON.is_file():
        raise RuntimeError(f"StockManager checkout and virtualenv required: {STOCK_SOURCE}")
    config_path = STOCK_SOURCE / "config" / f"{CONFIG_NAME}.json"
    if not config_path.is_file():
        raise RuntimeError(f"StockManager strategy config missing: {config_path}")
    if with_model:
        from dotenv import dotenv_values

        key = os.environ.get("DEEPSEEK_API_KEY") or dotenv_values(
            Path(__file__).resolve().parents[1] / ".env"
        ).get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("--with-model requires DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_API_KEY"] = key
    with tempfile.TemporaryDirectory(prefix="agent-paper-engine-") as folder:
        temporary = Path(folder)
        original_cwd = Path.cwd()
        os.chdir(temporary)
        stock_port, trading_port = free_local_port(), free_local_port()
        while trading_port == stock_port:
            trading_port = free_local_port()
        stock_root = temporary / "stock"
        shutil.copytree(STOCK_SOURCE / "stockmanager", stock_root / "stockmanager",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env*"))
        (stock_root / "config").mkdir()
        shutil.copy2(config_path, stock_root / "config" / config_path.name)
        server_script = temporary / "stock_server.py"
        server_script.write_text(STOCK_SERVER)
        stock_env = {key: value for key, value in os.environ.items()
                     if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}}
        stock_env.update(PYTHONPATH=str(stock_root), SYNTH_STOCK_ROOT=str(stock_root),
                         SYNTH_STOCK_PORT=str(stock_port))
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
                                  agent_model_planning_enabled=with_model,
                                  stockmanager_web_url=f"http://127.0.0.1:{stock_port}")
            if with_model:
                DEFAULT_CONFIG.update(llm_provider="deepseek",
                                      quick_think_llm="deepseek-v4-flash")
            app = create_app()
            trading_server = uvicorn.Server(uvicorn.Config(
                app, host="127.0.0.1", port=trading_port, log_level="error"))
            trading_task = asyncio.create_task(trading_server.serve())
            for _ in range(100):
                if trading_server.started:
                    break
                await asyncio.sleep(0.1)
            assert trading_server.started

            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{stock_port}", timeout=15) as sm, \
                    httpx.AsyncClient(base_url=f"http://127.0.0.1:{trading_port}", timeout=15) as ta:
                context = (await sm.get("/__test/context")).json()
                session_id = context["session_id"]
                path = f"/api/v2/paper/{quote(session_id, safe='')}/status"
                before = (await sm.get(path)).json()["data"]
                assert before["session"]["last_date"] == context["baseline"]
                assert before["snapshot"]["as_of_date"] == context["baseline"]
                created = await ta.post("/api/v1/agent/conversations", json={
                    "paper_session_id": session_id,
                })
                assert created.status_code == 201, created.text
                cid = created.json()["id"]
                submitted = await ta.post(f"/api/v1/agent/conversations/{cid}/tasks", json={
                    "message": f"推进模拟盘到 {context['target']}",
                })
                assert submitted.status_code == 202, submitted.text
                task_id = submitted.json()["id"]
                proposal = await wait_task(ta, task_id, {"awaiting_approval", "failed", "completed"})
                assert proposal["status"] == "awaiting_approval", proposal
                assert proposal["evidence"][0]["tool_name"] == "get_paper_session"
                assert proposal["evidence"][0]["as_of_date"] == context["baseline"]
                assert proposal["evidence"][0]["result"]["session_id"] == session_id
                proposal_id = proposal["proposal"]["id"]
                assert (await sm.get(path)).json()["data"]["session"]["last_date"] == context["baseline"]
                approved = await ta.post(f"/api/v1/agent/proposals/{proposal_id}/approve")
                assert approved.status_code == 200, approved.text
                task = await wait_task(ta, task_id, {"completed", "failed", "needs_review"})
                assert task["status"] == "completed", task
                assert task["proposal"]["status"] == "completed", task
                after = (await sm.get(path)).json()["data"]
                assert after["session"]["last_date"] == context["target"]
                assert after["snapshot"]["as_of_date"] == context["target"]
                result = task["proposal"]["result"]
                assert result["advanced_days"] == 10, result
                assert result["as_of_date"] == context["target"], result
                assert "action_completed" in [event["event_type"] for event in task["events"]]
                trades = (await sm.get(path.replace("/status", "/trades"))).json()["items"]
                advanced_trades = [item for item in trades
                                   if item["trade_date"] > context["baseline"]]
                assert advanced_trades, trades
                assert all(item["source"] == "paper" for item in advanced_trades)
                print(json.dumps({
                    "status": task["status"], "strategy": before["session"]["strategy"],
                    "baseline": context["baseline"], "target": context["target"],
                    "advanced_days": result["advanced_days"],
                    "new_trades": len(advanced_trades),
                    "equity_before": before["snapshot"]["equity"],
                    "equity_after": after["snapshot"]["equity"],
                }, ensure_ascii=False))
                if with_model:
                    read = await ta.post(f"/api/v1/agent/conversations/{cid}/tasks", json={
                        "message": "总结这个模拟盘当前的账本日期、权益、现金和近期成交。只报告已核对的事实。",
                    })
                    assert read.status_code == 202, read.text
                    read_task = await wait_task(ta, read.json()["id"],
                                                {"completed", "failed", "needs_input"})
                    assert read_task["status"] == "completed", read_task
                    assert read_task["proposal"] is None, read_task
                    assert read_task["evidence"] and all(
                        item["tool_name"] == "get_paper_session"
                        for item in read_task["evidence"]
                    ), read_task
                    assert read_task["evidence"][0]["as_of_date"] == context["target"]
                    answer = read_task["result"]["content"]
                    assert "模型暂时不可用" not in answer, answer
                    assert context["target"] in answer, answer
                    normalized_answer = answer.replace(",", "").replace(" ", "")
                    assert f"{after['snapshot']['equity']:.2f}" in normalized_answer, answer
                    assert f"{after['snapshot']['cash']:.2f}" in normalized_answer, answer
                    assert not any(field in answer for field in (
                        "state_fingerprint", "pending_stock", "phase39_signal_rebalance"
                    )), answer
                    plan_events = [item for item in read_task["events"]
                                   if item["event_type"] == "plan_created"]
                    assert len(plan_events) == 1, read_task
                    assert plan_events[0]["payload"]["source"] == "model", read_task
                    reread = (await sm.get(path)).json()["data"]
                    assert reread["state_fingerprint"] == after["state_fingerprint"]
                    print(json.dumps({"model_task_status": read_task["status"],
                                      "plan_source": plan_events[0]["payload"]["source"],
                                      "model_answer": answer}, ensure_ascii=False))
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-model", action="store_true",
                        help="Also check a read-only Agent answer using DeepSeek on synthetic data")
    asyncio.run(main(with_model=parser.parse_args().with_model))
