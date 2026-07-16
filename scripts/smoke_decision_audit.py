"""Run a live outcome-evaluation smoke against an explicitly supplied DB copy."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from tradingagents.core.decision_audit import DecisionAuditEngine, audit_summary
from tradingagents.core.mcp_client import shutdown_mcp_client
from tradingagents.core.persistence import Database
from tradingagents.default_config import DEFAULT_CONFIG


async def run(db_path: Path, as_of_date: str, limit: int) -> None:
    db = Database(db_path)
    try:
        result = await DecisionAuditEngine(db, dict(DEFAULT_CONFIG)).evaluate_due(
            as_of_date=as_of_date, limit=limit
        )
        print(json.dumps({"evaluation": result, "summary": audit_summary(db)}, ensure_ascii=False, indent=2))
    finally:
        await shutdown_mcp_client()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True, help="Writable database copy; never point this smoke at production")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(run(args.db, args.as_of_date, max(1, min(args.limit, 20))))


if __name__ == "__main__":
    main()
