"""Opt-in live StockManager MCP contract smoke.

Run with ``RUN_LIVE_MCP_TESTS=1 .venv/bin/python scripts/smoke_mcp_contract.py``.
The script is read-only: it probes health/capabilities and calls a small factor
snapshot. It exits non-zero when provenance or error-envelope invariants fail.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date

from tradingagents.core.mcp_client import MCPConfig, StockManagerMCPClient


async def main() -> None:
    if os.environ.get("RUN_LIVE_MCP_TESTS", "").lower() not in {"1", "true", "yes"}:
        raise SystemExit("Set RUN_LIVE_MCP_TESTS=1 to run the live MCP smoke")
    client = StockManagerMCPClient(
        MCPConfig(url=os.environ.get("STOCKMANAGER_MCP_URL", "http://127.0.0.1:8765/mcp"))
    )
    if not await client.connect():
        raise SystemExit(f"MCP connection failed: {client.status.error}")
    try:
        status = await client.refresh_status()
        if not status.tools:
            raise AssertionError("capabilities must expose a non-empty tools list")
        tool_schemas = {}
        if client._session is not None:  # live contract diagnostic
            listed = await client._session.list_tools()
            for tool in listed.tools:
                if tool.name in {"get_stock_daily", "run_backtest", "compute_purged_cv_sharpe"}:
                    tool_schemas[tool.name] = {
                        "description": getattr(tool, "description", None),
                        "input_schema": getattr(tool, "inputSchema", None),
                    }
        payload = await client.get_factor_snapshot(
            ["600519.SH"],
            os.environ.get("MCP_SMOKE_TRADE_DATE", date.today().isoformat()),
        )
        if not isinstance(payload, dict):
            raise AssertionError("get_factor_snapshot must return a JSON object")
        if payload.get("status") == "error":
            raise AssertionError(f"tool error envelope: {payload.get('error')}")
        missing = [key for key in ("as_of_date", "source") if not payload.get(key)]
        if missing:
            raise AssertionError(f"factor snapshot missing provenance: {missing}")
        stock_symbol = os.environ.get("MCP_SMOKE_SYMBOL", "600519.SH")
        start_date = os.environ.get("MCP_SMOKE_START", "20260701")
        end_date = os.environ.get("MCP_SMOKE_END", "20260711")
        stock = await client.get_stock_daily([stock_symbol], start_date, end_date, "qfq")
        index = await client.get_index_daily("000300.SH", start_date, end_date)
        catalog = await client.list_strategies_and_configs()
        print({
            "connected": True, "tool_count": len(status.tools),
            "tools": status.tools,
            "as_of_date": payload["as_of_date"], "source": payload["source"],
            "stock_daily": _shape(stock), "index_daily": _shape(index),
            "strategy_catalog": catalog,
            "tool_schemas": tool_schemas,
        })
    finally:
        await client.disconnect()


def _shape(payload):
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    rows = payload.get("rows") or payload.get("data") or []
    first = rows[0] if isinstance(rows, list) and rows else None
    nested = None
    if isinstance(first, dict):
        for key in ("rows", "data", "bars", "items"):
            if isinstance(first.get(key), list):
                nested = {"key": key, "count": len(first[key])}
                break
    return {
        "keys": sorted(payload),
        "status": payload.get("status"),
        "rows": len(rows),
        "first_row_keys": sorted(first) if isinstance(first, dict) else [],
        "first_row_type": type(first).__name__ if first is not None else None,
        "first_row_preview": str(first)[:200] if not isinstance(first, dict) else None,
        "nested": nested,
        "error": payload.get("error"),
    }


if __name__ == "__main__":
    asyncio.run(main())
