"""Acceptance smoke test for docs/MCP_ENHANCEMENT_REQUIREMENTS.md (R1-R5).

Runs each requirement's acceptance checks against a live StockManager MCP
server and prints a PASS/FAIL summary. Read-only; safe to re-run.

Usage:
    .venv/bin/python scripts/smoke_mcp_requirements.py [--trade-date YYYYMMDD]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from tradingagents.core.mcp_client import MCPConfig, StockManagerMCPClient

RESULTS: list[tuple[str, bool, str]] = []


def record(req: str, ok: bool, note: str) -> None:
    RESULTS.append((req, ok, note))
    print(f"  [{'PASS' if ok else 'FAIL'}] {req}: {note}")


def rows_of(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    # get_job_result wraps the tool payload in a {job_id, status, result} envelope.
    inner = payload.get("result")
    if isinstance(inner, dict):
        payload = inner
    for key in ("rows", "candidates", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


async def resolve_trade_date(client: StockManagerMCPClient, override: str | None) -> str:
    if override:
        return override
    payload = await client._call_tool(
        "get_trading_calendar", {"start_date": "20260701", "end_date": "20260730"}
    )
    days = [
        str(d.get("cal_date") or d)
        for d in rows_of(payload)
        if not isinstance(d, dict) or d.get("is_open") in (1, "1", True, None)
    ]
    if days:
        return sorted(days)[-1]
    return "20260729"


async def check_r2_sync_rank(client: StockManagerMCPClient, trade_date: str) -> list[dict]:
    print("\n== R2: rank_factor_candidates sync + latest_price/pct_chg ==")
    payload = await client._call_tool(
        "rank_factor_candidates",
        {
            "universe_index": "000300.SH",
            "trade_date": trade_date,
            "style": "medium_term",
            "limit": 5,
            "candidate_limit": 50,
            "return_factor_snapshot": False,
            "enable_decision": True,
        },
    )
    rows = rows_of(payload)
    if not rows:
        status = (payload or {}).get("status") if isinstance(payload, dict) else None
        record("R2", False, f"no rows returned (status={status}); cannot verify")
        return []
    priced = [r for r in rows if isinstance(r.get("latest_price"), (int, float))]
    has_pct = [r for r in rows if isinstance(r.get("pct_chg"), (int, float))]
    sample = rows[0]
    record(
        "R2",
        len(priced) == len(rows) and len(has_pct) == len(rows),
        f"{len(priced)}/{len(rows)} rows with latest_price, {len(has_pct)}/{len(rows)} with pct_chg; "
        f"sample {sample.get('ts_code')}: price={sample.get('latest_price')} pct={sample.get('pct_chg')}",
    )
    return rows


async def check_r1_async_rank(
    client: StockManagerMCPClient, trade_date: str, sync_rows: list[dict]
) -> None:
    print("\n== R1: async_mode job + progress ==")
    t0 = time.monotonic()
    submit = await client._call_tool(
        "rank_factor_candidates",
        {
            "universe_index": "000300.SH",
            "trade_date": trade_date,
            "style": "medium_term",
            "limit": 5,
            "candidate_limit": 50,
            "return_factor_snapshot": False,
            "enable_decision": True,
            "async_mode": True,
        },
    )
    submit_secs = time.monotonic() - t0
    job_id = (submit or {}).get("job_id") if isinstance(submit, dict) else None
    if not job_id:
        record("R1", False, f"no job_id in async submit response: {submit}")
        return
    record("R1-submit", submit_secs <= 2.0, f"job_id={job_id} in {submit_secs:.2f}s (<=2s required)")

    pcts: list[float] = []
    stages: list[str] = []
    progress_seen = False
    final_status = None
    # Poll up to ~4 minutes: the async job recomputes without the sync call's
    # warm cache, so it can be much slower than the sync baseline.
    for _ in range(240):
        status = await client._call_tool("get_job_status", {"job_id": job_id})
        if not isinstance(status, dict):
            break
        final_status = status.get("status")
        progress = status.get("progress")
        if isinstance(progress, dict):
            progress_seen = True
            if isinstance(progress.get("progress_pct"), (int, float)):
                pcts.append(float(progress["progress_pct"]))
            if progress.get("stage"):
                stages.append(str(progress["stage"]))
        if final_status in ("done", "completed", "succeeded", "success", "error", "failed"):
            break
        await asyncio.sleep(1.0)
    monotonic = all(a <= b for a, b in zip(pcts, pcts[1:], strict=False))
    terminal_ok = final_status in ("done", "completed", "succeeded", "success")
    record(
        "R1-progress",
        progress_seen and monotonic and terminal_ok,
        f"status={final_status}, stages={list(dict.fromkeys(stages))}, pct_samples={pcts[:8]}"
        + ("" if monotonic else " (NON-MONOTONIC)")
        + ("" if terminal_ok else " (JOB NOT TERMINAL WITHIN POLL WINDOW)"),
    )
    if not terminal_ok:
        record("R1-result", False, "skipped: job did not reach terminal state within poll window")
        return

    result = await client._call_tool("get_job_result", {"job_id": job_id})
    async_rows = rows_of(result)
    sync_codes = [r.get("ts_code") for r in sync_rows]
    async_codes = [r.get("ts_code") for r in async_rows]
    record(
        "R1-result",
        bool(async_rows) and async_codes == sync_codes,
        f"async rows={len(async_rows)}; ts_code order matches sync: {async_codes == sync_codes} "
        f"(sync={sync_codes}, async={async_codes})",
    )


async def check_r3_batch_risk(client: StockManagerMCPClient) -> None:
    print("\n== R3: get_risk_announcements batch vs single ==")
    codes = ["600519.SH", "300750.SZ", "000001.SZ"]
    common = {
        "start_date": "20260101",
        "end_date": "20260730",
        "keywords": ["减持", "问询", "处罚", "立案", "违规", "业绩", "预亏"],
    }
    t0 = time.monotonic()
    batch = await client._call_tool("get_risk_announcements", {"ts_codes": codes, **common})
    batch_secs = time.monotonic() - t0
    batch_rows = rows_of(batch)
    tagged = [r for r in batch_rows if r.get("ts_code")]
    singles: list[dict] = []
    for code in codes:
        single = await client._call_tool("get_risk_announcements", {"ts_code": code, **common})
        singles.extend(rows_of(single))
    record(
        "R3-batch",
        isinstance(batch, dict) and batch.get("status") in ("success", "partial") and len(tagged) == len(batch_rows),
        f"batch {len(batch_rows)} rows (all tagged ts_code: {len(tagged) == len(batch_rows)}) in {batch_secs:.2f}s",
    )
    record(
        "R3-parity",
        len(batch_rows) == len(singles),
        f"batch rows={len(batch_rows)} vs single-call union={len(singles)}",
    )


def check_r4_capabilities(capabilities: dict | None) -> None:
    print("\n== R4: capabilities contract_version / tool_features ==")
    caps = capabilities or {}
    features = caps.get("tool_features") or {}
    rank_features = set(features.get("rank_factor_candidates") or [])
    risk_features = set(features.get("get_risk_announcements") or [])
    ok = (
        bool(caps.get("contract_version"))
        and "tool_aliases" in caps
        and {"async_mode", "latest_price"} <= rank_features
        and "batch_ts_codes" in risk_features
    )
    record(
        "R4",
        ok,
        f"contract_version={caps.get('contract_version')}, rank features={sorted(rank_features)}, "
        f"risk features={sorted(risk_features)}",
    )


async def check_r5_signal_backtest(client: StockManagerMCPClient) -> None:
    print("\n== R5: run_signal_backtest excess metrics ==")
    payload = await client._call_tool(
        "run_signal_backtest",
        {
            "signals": [
                {
                    "trade_date": "2026-06-03",
                    "ts_code": "600519.SH",
                    "signal": "BUY",
                    "final_score": 82,
                    "quant_score": 78,
                    "llm_confidence": 88,
                    "horizon_days": 20,
                },
                {
                    "trade_date": "2026-06-05",
                    "ts_code": "300750.SZ",
                    "signal": "BUY",
                    "final_score": 75,
                    "quant_score": 80,
                    "llm_confidence": 70,
                    "horizon_days": 20,
                },
            ],
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
            "holding_rule": "horizon_or_stop",
            "cost_bps": 10,
            "benchmark": "000300.SH",
        },
    )
    if not isinstance(payload, dict) or payload.get("status") == "error":
        record("R5", False, f"call failed: {json.dumps(payload, ensure_ascii=False)[:200]}")
        return
    metrics = payload.get("metrics") or {}
    required = ["hit_rate", "avg_return", "excess_return", "avg_excess_return", "sharpe", "max_drawdown"]
    missing = [k for k in required if k not in metrics]
    sector = metrics.get("sector_excess_return", "ABSENT")
    warnings = payload.get("warnings") or []
    sector_ok = isinstance(sector, (int, float)) or (sector is None and warnings)
    record(
        "R5",
        not missing and "attribution" in payload and "recommendations" in payload and sector_ok,
        f"missing={missing or 'none'}, sector_excess_return={sector}, "
        f"attribution={'yes' if 'attribution' in payload else 'no'}, "
        f"recommendations={'yes' if 'recommendations' in payload else 'no'}, warnings={len(warnings)}",
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", default=None, help="Override trade date (YYYYMMDD)")
    args = parser.parse_args()

    client = StockManagerMCPClient(MCPConfig())
    if not await client.connect():
        print("FATAL: cannot connect to StockManager MCP; is the server running?")
        return 1
    try:
        check_r4_capabilities(client.status.capabilities)
        trade_date = await resolve_trade_date(client, args.trade_date)
        print(f"\nUsing trade_date={trade_date}")
        sync_rows = await check_r2_sync_rank(client, trade_date)
        await check_r1_async_rank(client, trade_date, sync_rows)
        await check_r3_batch_risk(client)
        await check_r5_signal_backtest(client)
    finally:
        await client.disconnect()

    print("\n== Summary ==")
    failed = [name for name, ok, _ in RESULTS if not ok]
    for name, ok, _ in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
