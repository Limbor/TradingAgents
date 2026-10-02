"""One-off backfill: re-attribute previously-reflected NEUTRAL decisions.

Neutral calls (WATCHLIST/HOLD/MONITOR) that were reflected before the
benchmark-relative neutral attribution existed are all ``noise``/``inconclusive``
with no ``excess_return``. This re-scores them as a backtest:

    fetch benchmark -> compute excess_return -> _neutral_attribution ->
    refresh attribution_payload / outcome_payload / lesson_payload.

It only touches cases whose stored decision is neutral and whose outcome has an
``actual_return``; directional cases are left untouched so the win-rate gate is
unaffected. Idempotent: a neutral lesson is created only once per case (re-runs
reuse the existing lesson). Use --dry-run to preview without writing.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from pathlib import Path

from tradingagents.core.persistence import DB_PATH, Database
from tradingagents.core.reflection import (
    ReflectionEngine,
    _neutral_attribution,
    _reflection_text_from_attribution,
)
from tradingagents.default_config import DEFAULT_CONFIG

NEUTRAL_TOKENS = ("WATCHLIST", "HOLD", "MONITOR")
_NEUTRAL_LESSON_TYPES = {"opportunity_cost", "risk_avoidance"}


def _decision(case: dict) -> str:
    snap = case.get("snapshot_payload") or {}
    return str(
        snap.get("final_decision")
        or snap.get("signal")
        or snap.get("decision")
        or ""
    ).upper()


def _is_neutral(case: dict) -> bool:
    return any(tok in _decision(case) for tok in NEUTRAL_TOKENS)


async def _backfill(db: Database, config: dict, *, dry_run: bool) -> None:
    engine = ReflectionEngine(db=db, config=config)
    reflected = db.list_reflection_cases(status="reflected", limit=5000)
    neutral = [c for c in reflected if _is_neutral(c)]
    todo = [
        c for c in neutral
        if isinstance((c.get("outcome_payload") or {}).get("actual_return"), (int, float))
    ]
    print(f"reflected={len(reflected)} neutral={len(neutral)} eligible_for_backfill={len(todo)}")

    label_counts: Counter = Counter()
    basis_counts: Counter = Counter()
    lessons_created = 0
    changed = 0

    for case in todo:
        outcome = dict(case.get("outcome_payload") or {})
        outcome["was_correct"] = None  # neutral, by construction
        signal_date = str(case.get("signal_date") or "")
        horizon = int(case.get("horizon_days") or 5)

        benchmark = await engine.fetch_benchmark_return(signal_date, horizon)
        if benchmark is not None and isinstance(benchmark.get("actual_return"), (int, float)):
            outcome["benchmark_symbol"] = benchmark.get("benchmark_symbol")
            outcome["benchmark_return"] = benchmark.get("actual_return")
            outcome["excess_return"] = round(
                float(outcome["actual_return"]) - float(benchmark["actual_return"]), 4
            )

        attribution = _neutral_attribution(case, outcome, {})
        label_counts[attribution["attribution"]] += 1
        basis_counts[attribution.get("basis", "?")] += 1

        # Reuse an existing neutral lesson so re-runs don't duplicate rows.
        existing_lesson = case.get("lesson_payload") or {}
        already_has_lesson = existing_lesson.get("lesson_type") in _NEUTRAL_LESSON_TYPES
        lesson = existing_lesson
        if not already_has_lesson:
            if dry_run:
                # Predict whether a lesson would be created without writing.
                if attribution["attribution"] in {"missed_upside", "validated_avoidance"} \
                        and str(attribution.get("confidence")) in {"medium", "high"}:
                    lessons_created += 1
                    lesson = {"_dry_run_would_create": attribution["attribution"]}
            else:
                lesson = engine.maybe_create_strategy_lesson(case, attribution)
                if lesson:
                    lessons_created += 1

        reflection_text = _reflection_text_from_attribution(attribution, outcome)
        excess = outcome.get("excess_return")
        excess_str = f"{excess:+.2%}" if isinstance(excess, (int, float)) else "n/a"
        print(
            f"  {case.get('symbol'):>10} {signal_date} "
            f"ret={float(outcome['actual_return']):+.2%} excess={excess_str} "
            f"-> {attribution['attribution']} ({attribution.get('confidence')}) | {reflection_text}"
        )

        if not dry_run:
            db.update_reflection_case(
                case["id"],
                status="reflected",
                outcome_payload=outcome,
                attribution_payload=attribution,
                lesson_payload=lesson or None,
            )
        changed += 1

    print("\n=== summary ===")
    print("processed        :", changed)
    print("attribution mix  :", dict(label_counts))
    print("metric basis     :", dict(basis_counts))
    print("lessons created  :", lessons_created, "(dry-run prediction)" if dry_run else "")
    if dry_run:
        print("\n[dry-run] no changes written. Re-run without --dry-run to apply.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--benchmark", default=None,
                        help="Override benchmark index (default: reflection_benchmark or 000300.SH).")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = {**DEFAULT_CONFIG}
    if args.benchmark:
        config["reflection_benchmark"] = args.benchmark
    db = Database(args.db)
    asyncio.run(_backfill(db, config, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
