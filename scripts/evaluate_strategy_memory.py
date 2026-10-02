"""Run an explicit model replay on frozen snapshots, with/without memory.

Usage: python scripts/evaluate_strategy_memory.py --input snapshots.json
       --output report.json --provider deepseek --model MODEL --db-path DB
Input: list of {trade_date, snapshot_as_of, candidate, excess_return}.
This command calls the chosen external model. Review input before running.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.core.llm_candidate_review import build_candidate_reviewer
from tradingagents.core.memory_evaluation import evaluate_memory_pairs
from tradingagents.core.persistence import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--style", default="medium_term")
    parser.add_argument("--save-report", action="store_true", help="Save read-only report to Library/Reflection UI")
    args = parser.parse_args()
    db = Database(args.db_path)
    config = {"llm_provider": args.provider, "quick_think_llm": args.model, "backend_url": args.base_url,
              "temperature": 0}

    def factory(day, lessons):
        return build_candidate_reviewer(config, style=args.style, trade_date=day, strategy_lessons=lessons)

    report = asyncio.run(evaluate_memory_pairs(json.loads(args.input.read_text()), db, factory,
                                              style=args.style, model_label=f"{args.provider}/{args.model}"))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    if args.save_report:
        db.save_artifact(artifact_id=str(uuid.uuid4()), run_id="", skill_id="memory_evaluation",
                         artifact_type="memory_evaluation", title="历史经验对照评测", payload=report,
                         summary=f"{report['total_pairs']} 对样本，{report['memory_pairs']} 对匹配经验")
    print(f"Report saved: {args.output} ({report['memory_pairs']} relevant pairs)")


if __name__ == "__main__":
    main()
