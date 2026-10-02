"""Validate realized DailyPipeline/reflection history from the application DB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tradingagents.core.decision_validation import validate_decision_history
from tradingagents.core.persistence import DB_PATH, Database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--min-samples", type=int, default=20)
    args = parser.parse_args()
    db = Database(args.db)
    cases = db.list_reflection_cases(
        status="reflected",
        lookback_days=args.lookback_days,
        limit=args.limit,
    )
    result = validate_decision_history(cases, min_samples=args.min_samples)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["strategy_claims_allowed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
