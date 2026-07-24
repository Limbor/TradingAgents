"""Review and promote candidate strategy lessons into production guidance.

The reflection engine and the offline eval harness
(``accumulate_eval_samples.py``) distil strategy lessons but leave them at
``governance_status="candidate"`` + ``active=False`` so nothing silently feeds
prediction. The daily pipeline only injects ACTIVE lessons
(``_load_strategy_lessons`` reads ``active_only=True``); once a lesson is
approved it starts matching live candidates in the LLM review prompt via
``candidate_lesson_hits`` (by scope/target).

This CLI is the human review gate. Approving an eval-derived
(``backtest_eval``) lesson intentionally lets *backtest* experience guide *live*
prediction — the whole point of pre-seeding the reflection library from
historical replay so the system behaves as if it had run for a long time. Each
listing shows the lesson's source (``backtest_eval`` vs ``live`` vs ``unknown``)
so you know what you are promoting before you promote it.

Usage:
    # list pending candidate lessons (default action, changes nothing)
    python scripts/review_strategy_lessons.py

    # approve specific lessons by id
    python scripts/review_strategy_lessons.py --approve <id> [<id> ...]

    # approve every candidate at >= medium confidence sourced from backtests
    python scripts/review_strategy_lessons.py --approve-all \
        --min-confidence medium --source backtest_eval

    # retire an active lesson so it stops being injected
    python scripts/review_strategy_lessons.py --deactivate <id> [<id> ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Add project root to path so the script runs standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.core.persistence import Database
from tradingagents.core.reflection_enroll import BACKTEST_EVAL_SOURCE_TYPE

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
CANDIDATE_STATUSES = {"candidate", "validated"}


def resolve_source(db: Database, lesson: dict[str, Any]) -> str:
    """Trace a lesson back to the reflection case that produced it.

    Neutral lessons persist ``case_id`` into their payload; resolving it tells
    us whether the lesson came from the offline eval harness (``backtest_eval``)
    or from live production reflection. Returns ``"unknown"`` when the case can
    no longer be resolved (e.g. purged, or a miner-produced lesson with only
    ``evidence_cases``).
    """
    payload = lesson.get("payload") or {}
    case_id = payload.get("case_id")
    if not case_id:
        evidence = payload.get("evidence_cases") or []
        case_id = evidence[0] if evidence else None
    if not case_id:
        return "unknown"
    case = db.get_reflection_case(case_id)
    if not case:
        return "unknown"
    source_type = str(case.get("source_type") or "")
    if source_type == BACKTEST_EVAL_SOURCE_TYPE:
        return "backtest_eval"
    return source_type or "live"


def list_candidate_lessons(db: Database, limit: int = 100) -> list[dict[str, Any]]:
    """Return lessons still awaiting review (candidate / validated, inactive)."""
    rows = db.list_strategy_lessons(limit=limit, active_only=False)
    return [
        row
        for row in rows
        if str(row.get("governance_status") or "") in CANDIDATE_STATUSES
        and not row.get("active")
    ]


def select_for_bulk(
    lessons: list[dict[str, Any]],
    *,
    min_confidence: str,
    lesson_type: str | None,
    source_filter: str,
    source_of,
) -> list[dict[str, Any]]:
    """Filter candidate lessons for ``--approve-all`` by confidence/type/source."""
    threshold = CONFIDENCE_ORDER.get(min_confidence, 0)
    selected: list[dict[str, Any]] = []
    for lesson in lessons:
        conf = str(lesson.get("confidence") or "low").lower()
        if CONFIDENCE_ORDER.get(conf, 0) < threshold:
            continue
        if lesson_type and str(lesson.get("lesson_type") or "") != lesson_type:
            continue
        if source_filter != "all" and source_of(lesson) != source_filter:
            continue
        selected.append(lesson)
    return selected


def _format_lesson(lesson: dict[str, Any], source: str) -> str:
    scope = lesson.get("scope") or "global"
    target = lesson.get("target") or "-"
    finding = str(lesson.get("finding") or "").replace("\n", " ").strip()
    suggested = str(lesson.get("suggested_adjustment") or "").replace("\n", " ").strip()
    return (
        f"- id={lesson.get('id')}\n"
        f"    type={lesson.get('lesson_type')}  scope={scope}:{target}  "
        f"confidence={lesson.get('confidence')}  source={source}\n"
        f"    finding: {finding}\n"
        f"    suggested: {suggested}"
    )


def _print_candidates(db: Database, lessons: list[dict[str, Any]]) -> None:
    if not lessons:
        print("No candidate lessons awaiting review.")
        return
    print(f"{len(lessons)} candidate lesson(s) awaiting review:\n")
    for lesson in lessons:
        print(_format_lesson(lesson, resolve_source(db, lesson)))
        print()


def _approve(db: Database, lesson_ids: list[str]) -> int:
    approved = 0
    for lesson_id in lesson_ids:
        if db.approve_strategy_lesson(lesson_id):
            approved += 1
            print(f"approved: {lesson_id}")
        else:
            print(f"skip (not a pending candidate): {lesson_id}")
    return approved


def _deactivate(db: Database, lesson_ids: list[str]) -> int:
    retired = 0
    for lesson_id in lesson_ids:
        if db.deactivate_strategy_lesson(lesson_id):
            retired += 1
            print(f"deactivated: {lesson_id}")
        else:
            print(f"skip (not found): {lesson_id}")
    return retired


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", default="", help="Override SQLite DB path (default ~/.tradingagents/app.db)")
    parser.add_argument("--limit", type=int, default=100, help="Max lessons to scan")
    parser.add_argument("--approve", nargs="+", metavar="ID", help="Approve these candidate lesson ids")
    parser.add_argument("--approve-all", action="store_true", help="Approve every matching candidate lesson")
    parser.add_argument(
        "--min-confidence",
        choices=["low", "medium", "high"],
        default="low",
        help="With --approve-all: only approve at or above this confidence",
    )
    parser.add_argument("--lesson-type", default=None, help="With --approve-all: restrict to this lesson_type")
    parser.add_argument(
        "--source",
        choices=["all", "backtest_eval", "live", "unknown"],
        default="all",
        help="With --approve-all / listing: restrict to this source",
    )
    parser.add_argument("--deactivate", nargs="+", metavar="ID", help="Retire these active lesson ids")
    return parser


def run(args: argparse.Namespace) -> int:
    db = Database(Path(args.db_path)) if args.db_path else Database()

    if args.deactivate:
        return 0 if _deactivate(db, args.deactivate) else 1

    if args.approve:
        return 0 if _approve(db, args.approve) else 1

    candidates = list_candidate_lessons(db, limit=args.limit)

    if args.approve_all:
        selected = select_for_bulk(
            candidates,
            min_confidence=args.min_confidence,
            lesson_type=args.lesson_type,
            source_filter=args.source,
            source_of=lambda lesson: resolve_source(db, lesson),
        )
        if not selected:
            print("No candidate lessons matched the given filters.")
            return 1
        print(f"Approving {len(selected)} candidate lesson(s)...")
        _approve(db, [str(lesson.get("id")) for lesson in selected])
        return 0

    if args.source != "all":
        candidates = [c for c in candidates if resolve_source(db, c) == args.source]
    _print_candidates(db, candidates)
    return 0


def main() -> None:
    raise SystemExit(run(build_arg_parser().parse_args()))


if __name__ == "__main__":
    main()
