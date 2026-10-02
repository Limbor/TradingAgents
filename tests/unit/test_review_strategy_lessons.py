"""Governance CLI tests: review + promote candidate strategy lessons.

Verifies the review gate that lets *approved* eval-derived lessons start guiding
prediction, while keeping unreviewed candidates out (``active=False``).
"""

import tempfile
from pathlib import Path

import pytest

from scripts.review_strategy_lessons import (
    list_candidate_lessons,
    resolve_source,
    select_for_bulk,
)
from tradingagents.core.persistence import Database
from tradingagents.core.reflection_enroll import BACKTEST_EVAL_SOURCE_TYPE


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "lessons.db")


def _save_candidate(db, lesson_id, *, confidence="medium", lesson_type="opportunity_cost", case_id=None):
    db.save_strategy_lesson(
        lesson_id=lesson_id,
        lesson_type=lesson_type,
        scope="industry",
        target="电子",
        finding=f"{lesson_id} finding",
        suggested_adjustment="loosen concentration gate",
        evidence_count=1,
        confidence=confidence,
        active=False,
        payload={"case_id": case_id} if case_id else {},
    )


def _save_eval_case(db, case_id, symbol="002371.SZ"):
    db.save_reflection_case(
        case_id=case_id,
        source_type=BACKTEST_EVAL_SOURCE_TYPE,
        reflection_scope="candidate_pool",
        eligible_for_strategy_learning=False,
        symbol=symbol,
        signal_date="2024-01-05",
        horizon_days=5,
        snapshot_payload={"industry": "电子"},
        outcome_payload={"actual_return": 0.05},
        status="reflected",
    )


def test_list_candidate_lessons_only_pending(db):
    _save_candidate(db, "l:candidate")
    db.save_strategy_lesson(
        lesson_id="l:active",
        lesson_type="risk_avoidance",
        scope="global",
        target="",
        finding="already approved",
        suggested_adjustment="keep",
        evidence_count=1,
        confidence="high",
        active=True,
        payload={},
    )

    pending = list_candidate_lessons(db)
    assert [row["id"] for row in pending] == ["l:candidate"]


def test_approve_promotes_candidate_into_active_guidance(db):
    _save_candidate(db, "l:1")
    assert db.list_strategy_lessons(active_only=True) == []

    assert db.approve_strategy_lesson("l:1") is True

    active = db.list_strategy_lessons(active_only=True)
    assert [row["id"] for row in active] == ["l:1"]
    assert active[0]["governance_status"] == "approved"
    assert active[0]["active"] is True
    # Once approved it no longer shows up in the review queue.
    assert list_candidate_lessons(db) == []


def test_select_for_bulk_respects_confidence_and_source(db):
    _save_eval_case(db, "backtest_eval:002371")
    _save_candidate(db, "l:high", confidence="high", case_id="backtest_eval:002371")
    _save_candidate(db, "l:low", confidence="low")

    candidates = list_candidate_lessons(db)
    selected = select_for_bulk(
        candidates,
        min_confidence="medium",
        lesson_type=None,
        source_filter="backtest_eval",
        source_of=lambda lesson: resolve_source(db, lesson),
    )
    assert [row["id"] for row in selected] == ["l:high"]


def test_resolve_source_labels_eval_vs_unknown(db):
    _save_eval_case(db, "backtest_eval:002371")
    _save_candidate(db, "l:eval", case_id="backtest_eval:002371")
    _save_candidate(db, "l:orphan", case_id=None)

    lessons = {row["id"]: row for row in list_candidate_lessons(db)}
    assert resolve_source(db, lessons["l:eval"]) == "backtest_eval"
    assert resolve_source(db, lessons["l:orphan"]) == "unknown"
