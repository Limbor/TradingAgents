"""Tests for the read-only evaluation scorecard (backtest_eval samples)."""

import tempfile
from pathlib import Path

import pytest

from tradingagents.core.persistence import Database
from tradingagents.core.reflection_enroll import BACKTEST_EVAL_SOURCE_TYPE


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "eval.db")


def _save_eval(db, case_id, *, symbol, quant, ret, style, source_type=BACKTEST_EVAL_SOURCE_TYPE):
    db.save_reflection_case(
        case_id=case_id,
        source_type=source_type,
        reflection_scope="exploratory",
        eligible_for_strategy_learning=False,
        symbol=symbol,
        signal_date="2024-01-05",
        horizon_days=5,
        snapshot_payload={
            "quant_score": quant,
            "final_decision": "BUY",
            "fusion_mode": "quant_only",
            "investment_style": style,
        },
        outcome_payload={"actual_return": ret, "excess_return": ret, "horizon_days": 5},
        status="reflected",
    )


def test_evaluation_scorecard_degrades_without_samples(db):
    scorecard = db.get_evaluation_scorecard(min_samples=5)
    assert scorecard["available"] is False
    assert scorecard["n_evaluated"] == 0


def test_evaluation_scorecard_reads_only_eval_and_buckets_by_style(db):
    # Six short_term eval samples where higher quant_score -> higher return
    # (positive RankIC), plus a live case that must be ignored.
    for i in range(6):
        _save_eval(
            db, f"e:{i}", symbol=f"60000{i}.SH",
            quant=40 + i * 10, ret=0.01 * i, style="short_term",
        )
    _save_eval(
        db, "live:1", symbol="600100.SH", quant=99, ret=-0.5,
        style="short_term", source_type="system_signal",
    )

    scorecard = db.get_evaluation_scorecard(min_samples=5)
    assert scorecard["available"] is True
    assert scorecard["n_evaluated"] == 6  # live case excluded

    by_style = scorecard.get("alpha_suggestions_by_style")
    assert by_style is not None
    assert "short_term" in by_style
    assert by_style["short_term"]["n_style"] == 6
