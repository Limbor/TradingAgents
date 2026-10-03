"""Short lived read checkpoints for explicit retries, never for ledger writes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

READ_CHECKPOINT_TOOLS = {"get_mcp_factor_snapshot", "get_mcp_risk_announcements"}
CHECKPOINT_TTL_SECONDS = 180


def checkpoint_key(tool: str, args: dict, config: dict) -> str | None:
    # Undated live queries must run again. Memory retrieval is also excluded.
    if tool not in READ_CHECKPOINT_TOOLS or not args.get("ts_code"):
        return None
    date = args.get("trade_date") if tool == "get_mcp_factor_snapshot" else args.get("end_date")
    if not date:
        return None
    policy = {key: config.get(key) for key in (
        "llm_provider", "model_policy", "backend_url", "data_vendors", "tool_vendors",
        "stockmanager_mcp_url", "stockmanager_web_url",
    )}
    return hashlib.sha256(json.dumps({"tool": tool, "args": args, "policy": policy},
                                     sort_keys=True, default=str).encode()).hexdigest()


def checkpoint_valid(record: dict, *, key: str, now=None) -> bool:
    try:
        stamp = datetime.fromisoformat(record["retrieved_at"])
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        return record.get("key") == key and 0 <= age <= CHECKPOINT_TTL_SECONDS
    except (KeyError, ValueError, TypeError):
        return False
