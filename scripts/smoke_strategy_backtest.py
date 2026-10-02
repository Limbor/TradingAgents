"""Opt-in live DailyPipeline rule-backtest contract smoke."""

from __future__ import annotations

import asyncio
import json
import os

from tradingagents.core.mcp_client import get_mcp_client, shutdown_mcp_client
from tradingagents.core.strategy_backtest import audit_backtest_result
from tradingagents.default_config import DEFAULT_CONFIG


async def main() -> None:
    if os.environ.get("RUN_LIVE_MCP_TESTS", "").lower() not in {"1", "true", "yes"}:
        raise SystemExit("Set RUN_LIVE_MCP_TESTS=1 to run the live backtest smoke")
    strategy_name = os.environ.get("MCP_BACKTEST_STRATEGY", "ff_residual_csi800_main")
    config_name = os.environ.get("MCP_BACKTEST_CONFIG", "prod_ff_residual_csi800_tv15")
    audit_config = {"strategy_name": strategy_name, "config_name": config_name}
    client = await get_mcp_client(dict(DEFAULT_CONFIG))
    if client is None:
        raise SystemExit("StockManager MCP unavailable")
    try:
        resume_job_id = os.environ.get("MCP_BACKTEST_JOB_ID", "")
        submitted = {"job_id": resume_job_id, "status": "resumed"} if resume_job_id else await client.run_backtest(
            strategy=strategy_name, config=config_name,
            start=os.environ.get("MCP_BACKTEST_START", "2026-06-01"),
            end=os.environ.get("MCP_BACKTEST_END", "2026-06-30"),
            initial_cash=1_000_000, universe_size=50,
        )
        if not isinstance(submitted, dict) or submitted.get("status") == "error":
            raise SystemExit(f"run_backtest rejected: {submitted}")
        job_id = str(submitted.get("job_id") or "")
        result = submitted
        if job_id:
            last_status = {}
            for _ in range(max(1, int(os.environ.get("MCP_BACKTEST_POLLS", "12")))):
                await asyncio.sleep(5)
                status = await client.get_job_status(job_id) or {}
                last_status = status
                state = str(status.get("status") or "").lower()
                if state in {"completed", "success", "succeeded", "done"}:
                    result = await client.get_job_result(job_id) or {}
                    break
                if state in {"failed", "error", "cancelled"}:
                    raise SystemExit(f"backtest job failed: {status}")
            else:
                print(json.dumps({"submitted": submitted, "status": "still_running", "remote": last_status}, ensure_ascii=False, default=str))
                return
        audited = await audit_backtest_result(client, result, audit_config)
        print(json.dumps({"submitted": submitted, "result": audited}, ensure_ascii=False, indent=2))
    finally:
        await shutdown_mcp_client()


if __name__ == "__main__":
    asyncio.run(main())
