"""Reflection-case source isolation tests (backtest_eval filtering)."""

import tempfile
from pathlib import Path

import pytest

from tradingagents.core.persistence import Database
from tradingagents.core.reflection_enroll import BACKTEST_EVAL_SOURCE_TYPE


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Database(Path(tmpdir) / "reflect.db")


def _save_reflected(
    db,
    case_id,
    *,
    source_type,
    symbol,
    quant,
    ret,
    style="medium_term",
    signal_date="2024-01-05",
):
    db.save_reflection_case(
        case_id=case_id,
        source_type=source_type,
        reflection_scope="candidate_pool",
        eligible_for_strategy_learning=False,
        symbol=symbol,
        signal_date=signal_date,
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


def test_list_reflection_cases_source_type_filter(db):
    _save_reflected(db, "e:1", source_type=BACKTEST_EVAL_SOURCE_TYPE, symbol="600000.SH", quant=80, ret=0.05)
    _save_reflected(db, "s:1", source_type="system_signal", symbol="600001.SH", quant=70, ret=0.02)

    only_eval = db.list_reflection_cases(source_type=BACKTEST_EVAL_SOURCE_TYPE)
    assert [c["id"] for c in only_eval] == ["e:1"]
    assert all(c["source_type"] == BACKTEST_EVAL_SOURCE_TYPE for c in only_eval)


def test_list_reflection_cases_exclude_source_types(db):
    _save_reflected(db, "e:1", source_type=BACKTEST_EVAL_SOURCE_TYPE, symbol="600000.SH", quant=80, ret=0.05)
    _save_reflected(db, "s:1", source_type="system_signal", symbol="600001.SH", quant=70, ret=0.02)
    _save_reflected(db, "a:1", source_type="stock_analysis", symbol="600002.SH", quant=60, ret=-0.01)

    rows = db.list_reflection_cases(exclude_source_types=(BACKTEST_EVAL_SOURCE_TYPE,))
    ids = {c["id"] for c in rows}
    assert ids == {"s:1", "a:1"}
    assert BACKTEST_EVAL_SOURCE_TYPE not in {c["source_type"] for c in rows}


def test_get_prediction_scorecard_excludes_eval(db):
    # Five live cases (enough to clear min_samples) + one eval case that must
    # NOT leak into the live prediction scorecard.
    for i in range(5):
        _save_reflected(
            db, f"s:{i}", source_type="system_signal",
            symbol=f"60000{i}.SH", quant=50 + i * 8, ret=0.01 * (i - 2),
        )
    _save_reflected(
        db, "e:1", source_type=BACKTEST_EVAL_SOURCE_TYPE,
        symbol="600100.SH", quant=95, ret=0.20,
    )

    scorecard = db.get_prediction_scorecard(min_samples=5)
    assert scorecard["available"] is True
    assert scorecard["n_evaluated"] == 5  # eval case excluded
