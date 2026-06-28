"""Test StockManager MCP Server connectivity and basic tool calls.

Usage:
    cd /Users/goujunhong/Documents/develop/TradingAgents
    source .venv/bin/activate
    python scripts/test_mcp_connection.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


STOCKMANAGER_DIR = "/Users/goujunhong/Documents/develop/StockManager"
TUSHARE_TOKEN = "240c6168b72fd1d42484a7e5e3df33d5b72869287ddecf43e7c53cde"


def _truncate(value, max_len: int = 300) -> str:
    s = str(value)
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"...(truncated, total {len(s)} chars)"


async def test_tools():
    """Connect to StockManager MCP and test data query tools."""
    server_params = StdioServerParameters(
        command="/Users/goujunhong/Documents/develop/StockManager/.venv/bin/python",
        args=["stockmanager-mcp/server.py"],
        env={
            "TUSHARE_TOKEN": TUSHARE_TOKEN,
            "PYTHONPATH": ".:stockmanager-mcp",
            **{k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV" and k != "PATH"},
        },
        cwd=STOCKMANAGER_DIR,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ------------------------------------------------------------------
            # 1. List all available tools
            # ------------------------------------------------------------------
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print(f"✅ Connected! Found {len(tool_names)} tools:")
            for name in tool_names:
                print(f"   - {name}")
            print()

            # ------------------------------------------------------------------
            # 2. Test get_trading_calendar (fast, no token needed for basic test)
            # ------------------------------------------------------------------
            print("=" * 60)
            print("2. Testing get_trading_calendar...")
            try:
                result = await session.call_tool(
                    "get_trading_calendar",
                    arguments={
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-28",
                    },
                )
                print(f"   ✅ Success: {_truncate(result.content)}")
            except Exception as e:
                print(f"   ❌ Error: {e}")
            print()

            # ------------------------------------------------------------------
            # 3. Test resolve_stock_name
            # ------------------------------------------------------------------
            print("=" * 60)
            print("3. Testing resolve_stock_name...")
            try:
                result = await session.call_tool(
                    "resolve_stock_name",
                    arguments={"codes": ["600519.SH", "000858.SZ", "300750.SZ"]},
                )
                print(f"   ✅ Success: {_truncate(result.content)}")
            except Exception as e:
                print(f"   ❌ Error: {e}")
            print()

            # ------------------------------------------------------------------
            # 4. Test get_stock_daily
            # ------------------------------------------------------------------
            print("=" * 60)
            print("4. Testing get_stock_daily (600519.SH 贵州茅台)...")
            try:
                result = await session.call_tool(
                    "get_stock_daily",
                    arguments={
                        "ts_codes": ["600519.SH"],
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-27",
                    },
                )
                print(f"   ✅ Success: {_truncate(result.content, 500)}")
            except Exception as e:
                print(f"   ❌ Error: {e}")
            print()

            # ------------------------------------------------------------------
            # 5. Test get_financial_metrics
            # ------------------------------------------------------------------
            print("=" * 60)
            print("5. Testing get_financial_metrics (600519.SH)...")
            try:
                result = await session.call_tool(
                    "get_financial_metrics",
                    arguments={
                        "ts_code": "600519.SH",
                        "end_date": "2026-03-31",
                    },
                )
                print(f"   ✅ Success: {_truncate(result.content)}")
            except Exception as e:
                print(f"   ❌ Error: {e}")
            print()

            # ------------------------------------------------------------------
            # 6. Test get_industry_map
            # ------------------------------------------------------------------
            print("=" * 60)
            print("6. Testing get_industry_map (600519.SH, 300750.SZ)...")
            try:
                result = await session.call_tool(
                    "get_industry_map",
                    arguments={"ts_codes": ["600519.SH", "300750.SZ", "000858.SZ"]},
                )
                print(f"   ✅ Success: {_truncate(result.content)}")
            except Exception as e:
                print(f"   ❌ Error: {e}")
            print()

            # ------------------------------------------------------------------
            # 7. Test list_strategies_and_configs
            # ------------------------------------------------------------------
            print("=" * 60)
            print("7. Testing list_strategies_and_configs...")
            try:
                result = await session.call_tool(
                    "list_strategies_and_configs", arguments={}
                )
                print(f"   ✅ Success: {_truncate(result.content, 500)}")
            except Exception as e:
                print(f"   ❌ Error: {e}")
            print()

            print("=" * 60)
            print("🎉 All tests completed!")


if __name__ == "__main__":
    asyncio.run(test_tools())
