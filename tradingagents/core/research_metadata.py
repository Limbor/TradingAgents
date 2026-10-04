"""Bounded company-industry metadata for memory matching, not market views."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


async def resolve_research_industry(symbol: str, config: dict) -> dict:
    result = {"symbol": symbol.upper().replace(".SS", ".SH"), "industry": "",
              "source": "StockManager industry map", "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "purpose": "memory_matching_only"}
    if not config.get("stockmanager_mcp_enabled", True) or not result["symbol"].endswith((".SH", ".SZ", ".BJ")):
        return {**result, "status": "unavailable"}
    try:
        from tradingagents.core.mcp_client import get_mcp_client

        async def lookup():
            client = await get_mcp_client(config)
            return await client.get_industry_map(ts_codes=[result["symbol"]]) if client else None

        payload = await asyncio.wait_for(lookup(), timeout=8)
        if isinstance(payload, dict) and not payload.get("error"):
            for key in ("industry_map", "mapping", "data", "rows"):
                if isinstance(payload.get(key), (dict, list)):
                    payload = payload[key]
                    break
            value = payload.get(result["symbol"]) if isinstance(payload, dict) else None
            if isinstance(payload, dict) and str(payload.get("ts_code") or payload.get("symbol")).upper() == result["symbol"]:
                value = payload.get("industry") or payload.get("industry_name")
            if isinstance(value, dict):
                value = value.get("industry") or value.get("industry_name")
            if isinstance(payload, list):
                row = next((row for row in payload if isinstance(row, dict) and
                            str(row.get("ts_code") or row.get("symbol")).upper() == result["symbol"]), {})
                value = row.get("industry") or row.get("industry_name")
            if isinstance(value, str) and value.strip():
                return {**result, "industry": value.strip()[:80], "status": "available"}
    except Exception:
        pass  # Metadata failure must not discard price/fundamental research.
    return {**result, "status": "unavailable"}
