#!/usr/bin/env python3
"""Replay persisted selection→stock-analysis pairs through reconciliation.

The command is read-only.  It opens SQLite with ``mode=ro`` and works for both
legacy artifacts (where alignment was not stored) and new artifacts carrying
the structured reconciliation result.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from tradingagents.core.decision_reconciliation import reconcile_selection_analysis


def load_stock_report_payloads(db_path: Path) -> list[dict[str, Any]]:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT id, subject_id, created_at, payload_json FROM artifacts "
            "WHERE artifact_type = 'stock_report' ORDER BY created_at"
        ).fetchall()
    payloads: list[dict[str, Any]] = []
    for artifact_id, subject_id, created_at, raw in rows:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        payloads.append(
            {
                "artifact_id": artifact_id,
                "subject_id": subject_id,
                "created_at": created_at,
                "payload": payload,
            }
        )
    return payloads


def build_report(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for artifact in artifacts:
        payload = artifact.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        selection = payload.get("selection_context")
        conclusion = payload.get("structured_conclusion")
        if not isinstance(selection, dict) or not selection:
            continue
        conclusion = conclusion if isinstance(conclusion, dict) else {}
        alignment = reconcile_selection_analysis(
            selection,
            conclusion,
            analysis_date=str(payload.get("analysis_date") or "") or None,
        )
        records.append(
            {
                "artifact_id": artifact.get("artifact_id"),
                "symbol": payload.get("ticker") or artifact.get("subject_id"),
                "selection_decision": alignment["selection_decision"],
                "analysis_rating": alignment["analysis_rating"],
                "status": alignment["status"],
                "requires_review": alignment["requires_review"],
                "explicit_explanation": alignment["explicit_explanation"],
                "plan_consistency": alignment["plan_consistency"]["status"],
                "selection_trade_date": alignment["selection_trade_date"],
                "analysis_date": alignment["analysis_date"],
            }
        )

    statuses = Counter(str(record["status"]) for record in records)
    return {
        "total_stock_reports": len(artifacts),
        "paired_reports": len(records),
        "status_counts": dict(sorted(statuses.items())),
        "requires_review": sum(bool(record["requires_review"]) for record in records),
        "unexplained_review": sum(
            bool(record["requires_review"]) and not bool(record["explicit_explanation"])
            for record in records
        ),
        "plan_conflicts": sum(record["plan_consistency"] == "conflict" for record in records),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare persisted stock-selection decisions with stock-analysis ratings."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("~/.tradingagents/app.db").expanduser(),
        help="TradingAgents SQLite database (opened read-only).",
    )
    parser.add_argument("--json", action="store_true", help="Print the full machine-readable report.")
    args = parser.parse_args()

    if not args.db.is_file():
        parser.error(f"database does not exist: {args.db}")
    report = build_report(load_stock_report_payloads(args.db))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "selection-analysis compare: "
            f"reports={report['total_stock_reports']} paired={report['paired_reports']} "
            f"review={report['requires_review']} unexplained={report['unexplained_review']} "
            f"plan_conflicts={report['plan_conflicts']} statuses={report['status_counts']}"
        )
        for record in report["records"]:
            marker = "REVIEW" if record["requires_review"] else "OK"
            print(
                f"[{marker}] {record['symbol']}: {record['selection_decision']} -> "
                f"{record['analysis_rating']} ({record['status']})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
