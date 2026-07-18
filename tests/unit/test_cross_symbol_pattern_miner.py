"""Unit tests for CrossSymbolPatternMiner."""

from unittest.mock import MagicMock

import pytest

from tradingagents.core.cross_symbol_pattern_miner import (
    CrossSymbolPatternMiner,
    PatternBucket,
    _lesson_id_from_finding,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_case(
    symbol: str = "600519.SH",
    final_decision: str = "BUY",
    quant_score: float = 80.0,
    industry: str = "白酒",
    board: str = "main",
    data_coverage: dict | None = None,
    catalyst_strength: str = "confirmed",
    risk_assessment: str = "low",
    attribution_type: str = "ex_ante_miss",
    attr_confidence: str = "high",
    was_correct: bool | None = True,
    actual_return: float = 0.05,
) -> dict:
    """Build a mock reflected case with full payloads."""
    dc = data_coverage
    if dc is None:
        dc = {"valuation": "available", "flow": "available", "quality": "available"}

    snapshot = {
        "final_decision": final_decision,
        "quant_score": quant_score,
        "industry": industry,
        "board": board,
        "data_coverage": dc,
        "catalyst_strength": catalyst_strength,
        "risk_assessment": risk_assessment,
        "risk_flags": [],
        "llm_view": "strong_positive",
    }
    outcome = {
        "was_correct": was_correct,
        "actual_return": actual_return,
    }
    attribution = {
        "attribution": attribution_type,
        "confidence": attr_confidence,
    }
    return {
        "id": f"case_{symbol}",
        "symbol": symbol,
        "name": symbol,
        "signal_date": "2026-06-01",
        "status": "reflected",
        "snapshot_payload": snapshot,
        "outcome_payload": outcome,
        "attribution_payload": attribution,
    }


# ---------------------------------------------------------------------------
# Feature extraction tests
# ---------------------------------------------------------------------------


class TestFeatureExtraction:
    def test_extract_basic_features(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        case = _make_case(
            symbol="600519.SH",
            final_decision="BUY",
            quant_score=80,
            industry="白酒",
            catalyst_strength="confirmed",
            risk_assessment="low",
        )
        feats = miner._extract_features(case)
        assert feats["final_decision"] == "BUY"
        assert feats["quant_score"] == "75+"
        assert feats["industry"] == "白酒"
        assert feats["catalyst_strength"] == "confirmed"
        assert feats["risk_assessment"] == "low"

    def test_quant_score_buckets(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        assert miner._extract_features(_make_case(quant_score=50))["quant_score"] == "<60"
        assert miner._extract_features(_make_case(quant_score=70))["quant_score"] == "60-75"
        assert miner._extract_features(_make_case(quant_score=90))["quant_score"] == "75+"

    def test_industry_normalization(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        # "新能源电池" should normalize to "新能源"
        feats = miner._extract_features(_make_case(industry="新能源电池"))
        assert feats["industry"] == "新能源"

    def test_data_coverage_features(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        case = _make_case(data_coverage={
            "valuation": "available",
            "flow": "missing",
            "quality": "available",
        })
        feats = miner._extract_features(case)
        assert feats["data_coverage.valuation"] == "available"
        assert feats["data_coverage.flow"] == "missing"
        assert feats["data_coverage.quality"] == "available"

    def test_return_buckets(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        assert miner._extract_features(_make_case(actual_return=-0.10))["return"] == "<-5%"
        assert miner._extract_features(_make_case(actual_return=0.01))["return"] == "-5~+3%"
        assert miner._extract_features(_make_case(actual_return=0.08))["return"] == ">+3%"

    def test_attribution_features(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        case = _make_case(attribution_type="ex_post_shock", attr_confidence="medium")
        feats = miner._extract_features(case)
        assert feats["attribution"] == "ex_post_shock"
        assert feats["attribution_confidence"] == "medium"


# ---------------------------------------------------------------------------
# Aggregation tests
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_basic_aggregation(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        cases = [
            _make_case(symbol="A", final_decision="BUY", was_correct=True, actual_return=0.05),
            _make_case(symbol="B", final_decision="BUY", was_correct=True, actual_return=0.03),
            _make_case(symbol="C", final_decision="BUY", was_correct=False, actual_return=-0.02),
            _make_case(symbol="D", final_decision="BUY", was_correct=True, actual_return=0.10),
            _make_case(symbol="E", final_decision="BUY", was_correct=True, actual_return=0.04),
        ]
        feats = [miner._extract_features(c) for c in cases]
        buckets = miner._aggregate(cases, feats)
        assert len(buckets) > 0

        # Find the BUY bucket
        buy_bucket = next((b for b in buckets if b.dimension == "final_decision=BUY"), None)
        assert buy_bucket is not None
        assert buy_bucket.n == 5
        assert buy_bucket.win_rate == 0.8  # 4 correct out of 5
        assert buy_bucket.avg_return == pytest.approx(0.04)

    def test_empty_cases(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        buckets = miner._aggregate([], [])
        assert buckets == []


# ---------------------------------------------------------------------------
# Significance filtering tests
# ---------------------------------------------------------------------------


class TestSignificanceFiltering:
    def test_filter_significant(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        buckets = [
            PatternBucket(dimension="A", n=10, scored_n=10, win_rate=0.80),
            PatternBucket(dimension="B", n=3, scored_n=3, win_rate=0.90),
            PatternBucket(dimension="C", n=8, scored_n=8, win_rate=0.55),
        ]
        baseline = 0.60
        result = miner._filter_significant(
            buckets, min_samples=5, min_lift=0.15, baseline_win_rate=baseline
        )
        dims = {b.dimension for b in result}
        assert "A" in dims  # scored_n=10, win_rate=0.80, lift=+0.20 → significant
        assert "B" not in dims  # scored_n=3 < min_samples=5
        assert "C" not in dims  # scored_n=8, win_rate=0.55, lift=-0.05 → |lift| < 0.15

    def test_filter_all_below_threshold(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        buckets = [
            PatternBucket(dimension="X", n=10, scored_n=10, win_rate=0.61),
            PatternBucket(dimension="Y", n=10, scored_n=10, win_rate=0.59),
        ]
        baseline = 0.60
        result = miner._filter_significant(
            buckets, min_samples=5, min_lift=0.15, baseline_win_rate=baseline
        )
        assert len(result) == 0


# Note: _filter_significant is an instance method, tested via miner instance above.


# ---------------------------------------------------------------------------
# Template explanation tests
# ---------------------------------------------------------------------------


class TestTemplateExplanation:
    def test_template_positive_lift(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        bucket = PatternBucket(
            dimension="final_decision=BUY",
            n=10,
            win_rate=0.80,
            baseline_win_rate=0.60,
            lift=0.20,
        )
        finding, adjustment = miner._template_explain(bucket)
        assert "final_decision=BUY" in finding
        assert "80%" in finding
        assert "高于" in finding
        assert "20%" in finding
        assert adjustment  # non-empty

    def test_template_negative_lift(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        bucket = PatternBucket(
            dimension="data_coverage.flow=missing+BUY",
            n=15,
            win_rate=0.25,
            baseline_win_rate=0.60,
            lift=-0.35,
        )
        finding, adjustment = miner._template_explain(bucket)
        assert "25%" in finding
        assert "低于" in finding
        assert "35%" in finding
        assert adjustment


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------


class TestDedupHelpers:
    def test_lesson_id_stable(self):
        id1 = _lesson_id_from_finding("高估值+资金流缺失胜率下降")
        id2 = _lesson_id_from_finding("高估值+资金流缺失胜率下降")
        assert id1 == id2
        assert id1.startswith("csp_")

    def test_lesson_id_different(self):
        id1 = _lesson_id_from_finding("pattern A")
        id2 = _lesson_id_from_finding("pattern B")
        assert id1 != id2


# ---------------------------------------------------------------------------
# Persistence extension tests
# ---------------------------------------------------------------------------


class TestPersistenceUpdates:
    """These tests verify the persistence methods work correctly."""

    def test_deactivate_strategy_lesson(self):
        """Test that deactivate sets active=0."""
        import os
        import tempfile
        from pathlib import Path

        from tradingagents.core.persistence import Database

        db_path = Path(os.path.join(tempfile.mkdtemp(), "test.db"))
        db = Database(db_path)

        # Create a test lesson
        db.save_strategy_lesson(
            lesson_id="test_lesson_1",
            lesson_type="cross_symbol_pattern",
            scope="global",
            finding="Test finding",
            suggested_adjustment="Test adjustment",
            evidence_count=1,
            confidence="medium",
        )

        # Verify it exists and is active
        lessons = db.list_strategy_lessons(active_only=True)
        assert any(lesson["id"] == "test_lesson_1" for lesson in lessons)

        # Deactivate
        result = db.deactivate_strategy_lesson("test_lesson_1")
        assert result is True

        # Verify it's now inactive
        active = db.list_strategy_lessons(active_only=True)
        assert not any(lesson["id"] == "test_lesson_1" for lesson in active)

        all_lessons = db.list_strategy_lessons(active_only=False)
        assert any(lesson["id"] == "test_lesson_1" for lesson in all_lessons)

    def test_deactivate_nonexistent(self):
        import os
        import tempfile
        from pathlib import Path

        from tradingagents.core.persistence import Database

        db_path = Path(os.path.join(tempfile.mkdtemp(), "test.db"))
        db = Database(db_path)

        result = db.deactivate_strategy_lesson("nonexistent")
        assert result is False

    def test_update_strategy_lesson_accumulate_evidence(self):
        import os
        import tempfile
        from pathlib import Path

        from tradingagents.core.persistence import Database

        db_path = Path(os.path.join(tempfile.mkdtemp(), "test.db"))
        db = Database(db_path)

        db.save_strategy_lesson(
            lesson_id="test_lesson_2",
            lesson_type="ex_ante_miss",
            scope="global",
            finding="Original finding",
            evidence_count=3,
            confidence="low",
        )

        # Update evidence_count (should accumulate: 3 + 2 = 5)
        updated = db.update_strategy_lesson(
            "test_lesson_2",
            evidence_count=2,
            confidence="medium",
        )
        assert updated is not None
        assert updated["evidence_count"] == 5
        assert updated["confidence"] == "medium"

    def test_update_strategy_lesson_nonexistent(self):
        import os
        import tempfile
        from pathlib import Path

        from tradingagents.core.persistence import Database

        db_path = Path(os.path.join(tempfile.mkdtemp(), "test.db"))
        db = Database(db_path)

        updated = db.update_strategy_lesson("nonexistent", finding="new")
        assert updated is None


# ---------------------------------------------------------------------------
# win_rate None handling (P0-2 fix)
# ---------------------------------------------------------------------------


class TestWinRateNoneHandling:
    """Neutral decisions (was_correct=None) must NOT dilute win_rate."""

    def test_neutral_decisions_do_not_dilate_win_rate(self):
        """3 cases: 1 win, 1 neutral (None), 1 loss → win_rate must be 0.5, not 0.33."""
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        cases = [
            _make_case(symbol="A", final_decision="BUY", was_correct=True, actual_return=0.05),
            _make_case(symbol="B", final_decision="BUY", was_correct=None, actual_return=0.0),
            _make_case(symbol="C", final_decision="BUY", was_correct=False, actual_return=-0.05),
        ]
        feats = [miner._extract_features(c) for c in cases]
        buckets = miner._aggregate(cases, feats)

        buy_bucket = next(b for b in buckets if b.dimension == "final_decision=BUY")
        # Total n includes the neutral case, but win_rate is over scored cases only.
        assert buy_bucket.n == 3
        assert buy_bucket.scored_n == 2
        assert buy_bucket.correct_count == 1
        assert buy_bucket.win_rate == 0.5  # 1 correct / 2 scored, NOT 1/3

    def test_all_neutral_bucket_has_zero_win_rate(self):
        """A bucket where every case is neutral must have win_rate 0.0 (not 1.0)."""
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        cases = [
            _make_case(symbol="A", final_decision="BUY", was_correct=None, actual_return=0.0),
            _make_case(symbol="B", final_decision="BUY", was_correct=None, actual_return=0.0),
        ]
        feats = [miner._extract_features(c) for c in cases]
        buckets = miner._aggregate(cases, feats)
        buy_bucket = next(b for b in buckets if b.dimension == "final_decision=BUY")
        assert buy_bucket.scored_n == 0
        assert buy_bucket.win_rate == 0.0


# ---------------------------------------------------------------------------
# mine() end-to-end (P0-3 dedup fix + pipeline)
# ---------------------------------------------------------------------------


class TestMineEndToEnd:
    """Verify the full mine() pipeline creates lessons and dedups by dimension."""

    @pytest.mark.asyncio
    async def test_mine_creates_lesson_for_significant_pattern(self):
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        # 6 BUY wins + 2 BUY losses → BUY bucket win_rate 0.75; baseline ~0.75
        # so lift ~0. To get a significant pattern, make flow=missing all lose.
        cases = []
        for i in range(6):
            cases.append(_make_case(
                symbol=f"WIN{i}", final_decision="BUY",
                data_coverage={"valuation": "available", "flow": "available", "quality": "available"},
                was_correct=True, actual_return=0.05,
            ))
        for i in range(6):
            cases.append(_make_case(
                symbol=f"LOSE{i}", final_decision="BUY",
                data_coverage={"valuation": "available", "flow": "missing", "quality": "available"},
                was_correct=False, actual_return=-0.06,
            ))

        db = MagicMock()
        db.list_reflection_cases = MagicMock(return_value=cases)
        db.list_strategy_lessons = MagicMock(return_value=[])
        db.save_strategy_lesson = MagicMock(return_value=None)
        db.update_strategy_lesson = MagicMock(return_value={"id": "x"})
        miner._db = db

        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        assert result["total_cases"] == 12
        assert result["significant_buckets"] >= 1
        assert result["lessons_created"] >= 1
        # The flow=missing bucket should be among the significant ones (win_rate 0).
        assert db.save_strategy_lesson.called or db.update_strategy_lesson.called

    @pytest.mark.asyncio
    async def test_mine_dedups_by_dimension_not_finding(self):
        """Re-running mine on the same dimension must update, not create a duplicate."""
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        # flow=missing all lose, flow=available all win → baseline 0.5, flow=missing lift -0.5.
        cases = []
        for i in range(6):
            cases.append(_make_case(
                symbol=f"LOSE{i}", final_decision="BUY",
                data_coverage={"valuation": "available", "flow": "missing", "quality": "available"},
                was_correct=False, actual_return=-0.06,
            ))
        for i in range(6):
            cases.append(_make_case(
                symbol=f"WIN{i}", final_decision="BUY",
                data_coverage={"valuation": "available", "flow": "available", "quality": "available"},
                was_correct=True, actual_return=0.05,
            ))

        # Simulate an existing lesson for the SAME dimension (different finding text).
        existing_lesson = {
            "id": "csp_old",
            "lesson_type": "cross_symbol_pattern",
            "finding": "近30天，data_coverage.flow=missing 的候选胜率 10%（旧文本）",
            "payload": {"dimension": "data_coverage.flow=missing"},
        }
        db = MagicMock()
        db.list_reflection_cases = MagicMock(return_value=cases)
        db.list_strategy_lessons = MagicMock(return_value=[existing_lesson])
        db.save_strategy_lesson = MagicMock(return_value=None)
        db.update_strategy_lesson = MagicMock(return_value={"id": "csp_old"})
        miner._db = db

        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        # The flow=missing dimension matched the existing lesson → updated, not created.
        assert result["lessons_updated"] >= 1
        assert db.update_strategy_lesson.called
        # No NEW lesson should have been saved for the flow=missing dimension
        # (it was merged into the existing one).
        saved_dims = [
            call.kwargs.get("payload", {}).get("dimension")
            for call in db.save_strategy_lesson.call_args_list
        ]
        assert "data_coverage.flow=missing" not in saved_dims, (
            "flow=missing dimension should update existing lesson, not save a new one"
        )

    @pytest.mark.asyncio
    async def test_mine_does_not_merge_different_dimensions(self):
        """Two different dimensions must produce two separate lessons (P0-3 fix)."""
        miner = CrossSymbolPatternMiner(db=MagicMock(), config={})
        # flow=missing+risk=high all lose; flow=available+risk=low all win.
        # → flow=missing lift -0.5, risk=high lift -0.5 (both significant, different dims).
        cases = []
        for i in range(6):
            cases.append(_make_case(
                symbol=f"L{i}", final_decision="BUY",
                data_coverage={"valuation": "available", "flow": "missing", "quality": "available"},
                risk_assessment="high", was_correct=False, actual_return=-0.06,
            ))
        for i in range(6):
            cases.append(_make_case(
                symbol=f"W{i}", final_decision="BUY",
                data_coverage={"valuation": "available", "flow": "available", "quality": "available"},
                risk_assessment="low", was_correct=True, actual_return=0.05,
            ))

        db = MagicMock()
        db.list_reflection_cases = MagicMock(return_value=cases)
        db.list_strategy_lessons = MagicMock(return_value=[])
        saved = []
        db.save_strategy_lesson = MagicMock(side_effect=lambda **kw: saved.append(kw) or {"id": kw["lesson_id"]})
        db.update_strategy_lesson = MagicMock(return_value={"id": "x"})
        miner._db = db

        await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        # Both dimensions should produce distinct lessons (not merged into one).
        dimensions_saved = [s["payload"]["dimension"] for s in saved]
        assert "data_coverage.flow=missing" in dimensions_saved
        assert "risk_assessment=high" in dimensions_saved
        assert len(saved) >= 2


# ---------------------------------------------------------------------------
# Neutral channel (WATCHLIST/HOLD/MONITOR excess-return patterns)
# ---------------------------------------------------------------------------


def _neutral_case(
    symbol: str,
    industry: str = "有色",
    excess: float = -0.15,
    board: str = "main",
    data_coverage: dict | None = None,
    signal_date: str = "2026-06-01",
) -> dict:
    """Build a reflected NEUTRAL case (was_correct=None) with an excess_return."""
    dc = data_coverage or {"valuation": "available", "flow": "available", "quality": "available"}
    snapshot = {
        "final_decision": "WATCHLIST",
        "quant_score": 70,
        "industry": industry,
        "board": board,
        "data_coverage": dc,
        "risk_flags": [],
        "catalyst_strength": "speculative",
        "risk_assessment": "high",
    }
    outcome = {
        "was_correct": None,
        "actual_return": excess,
        "excess_return": excess,
    }
    attribution = {
        "attribution": "validated_avoidance" if excess < 0 else "missed_upside",
        "confidence": "medium",
    }
    return {
        "id": f"case_{symbol}",
        "symbol": symbol,
        "name": symbol,
        "signal_date": signal_date,
        "status": "reflected",
        "snapshot_payload": snapshot,
        "outcome_payload": outcome,
        "attribution_payload": attribution,
    }


# Two ISO weeks (2026-W23 and 2026-W25) so neutral buckets built by cycling
# these dates span >= cross_symbol_miner_neutral_min_periods (2) distinct
# weeks and clear the regime guardrail.
_TWO_WEEK_DATES = ["2026-06-01", "2026-06-02", "2026-06-15", "2026-06-16"]


def _spread(i: int) -> str:
    """Signal date for the i-th case, cycling across two distinct ISO weeks."""
    return _TWO_WEEK_DATES[i % len(_TWO_WEEK_DATES)]


def _neutral_db(cases: list[dict]) -> MagicMock:
    db = MagicMock()
    db.list_reflection_cases = MagicMock(return_value=cases)
    db.list_strategy_lessons = MagicMock(return_value=[])
    saved: list[dict] = []
    db.save_strategy_lesson = MagicMock(
        side_effect=lambda **kw: saved.append(kw) or {"id": kw["lesson_id"]}
    )
    db.update_strategy_lesson = MagicMock(return_value={"id": "x"})
    db._saved = saved
    return db


class TestNeutralChannel:
    """Neutral cases (was_correct=None) promote by consistent excess return."""

    @pytest.mark.asyncio
    async def test_validated_avoidance_promoted_with_industry_scope(self):
        cases = [
            _neutral_case(f"N{i}", industry="有色", excess=-0.15, signal_date=_spread(i))
            for i in range(6)
        ]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(db=db, config={})
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        # Directional channel sees no scored cases (all was_correct=None).
        assert result["significant_buckets"] == 0
        assert result["neutral_significant_buckets"] >= 1
        by_dim = {s["payload"]["dimension"]: s for s in db._saved}
        assert "neutral:industry=有色" in by_dim
        lesson = by_dim["neutral:industry=有色"]
        assert lesson["scope"] == "industry"
        assert lesson["target"] == "有色"
        assert lesson["active"] is True
        assert lesson["payload"]["kind"] == "neutral"
        assert lesson["payload"]["pattern"] == "validated_avoidance"
        assert lesson["confidence"] == "high"  # |avg_excess| 0.15 >= 0.10

    @pytest.mark.asyncio
    async def test_missed_upside_promoted(self):
        cases = [
            _neutral_case(f"P{i}", industry="白酒", excess=0.12, signal_date=_spread(i))
            for i in range(6)
        ]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(db=db, config={})
        await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        by_dim = {s["payload"]["dimension"]: s for s in db._saved}
        assert "neutral:industry=白酒" in by_dim
        assert by_dim["neutral:industry=白酒"]["payload"]["pattern"] == "missed_upside"

    @pytest.mark.asyncio
    async def test_factor_scope_from_missing_coverage(self):
        dc = {"valuation": "available", "flow": "missing", "quality": "available"}
        cases = [
            _neutral_case(f"F{i}", excess=-0.12, data_coverage=dc, signal_date=_spread(i))
            for i in range(6)
        ]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(db=db, config={})
        await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        by_dim = {s["payload"]["dimension"]: s for s in db._saved}
        assert "neutral:data_coverage.flow=missing" in by_dim
        factor_lesson = by_dim["neutral:data_coverage.flow=missing"]
        assert factor_lesson["scope"] == "factor"
        assert factor_lesson["target"] == "flow"

    @pytest.mark.asyncio
    async def test_inconsistent_direction_not_promoted(self):
        # 3× +0.30, 3× -0.05 → avg +0.125 (>=0.05) but same-sign consistency
        # is only 3/6=0.5 (< 0.6) → must NOT promote.
        cases = [_neutral_case(f"A{i}", industry="有色", excess=0.30) for i in range(3)]
        cases += [_neutral_case(f"B{i}", industry="有色", excess=-0.05) for i in range(3)]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(db=db, config={})
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        assert result.get("neutral_significant_buckets", 0) == 0
        assert not any(
            str(s["payload"]["dimension"]).startswith("neutral:") for s in db._saved
        )

    @pytest.mark.asyncio
    async def test_below_excess_threshold_not_promoted(self):
        # Consistent sign but |avg_excess| 0.02 < 0.05 → not material enough.
        cases = [_neutral_case(f"S{i}", industry="有色", excess=-0.02) for i in range(6)]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(db=db, config={})
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        assert result.get("neutral_significant_buckets", 0) == 0

    @pytest.mark.asyncio
    async def test_neutral_disabled_skips_channel(self):
        cases = [_neutral_case(f"N{i}", industry="有色", excess=-0.15) for i in range(6)]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(
            db=db, config={"cross_symbol_miner_neutral_enabled": False}
        )
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        assert "neutral_significant_buckets" not in result
        assert not any(
            str(s["payload"]["dimension"]).startswith("neutral:") for s in db._saved
        )

    @pytest.mark.asyncio
    async def test_too_few_neutral_cases_not_promoted(self):
        cases = [_neutral_case(f"N{i}", industry="有色", excess=-0.15) for i in range(4)]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(db=db, config={})
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        assert result["neutral_significant_buckets"] == 0

    @pytest.mark.asyncio
    async def test_neutral_min_samples_config_honoured(self):
        # 4 consistent neutral cases: promoted at neutral_min_samples=4 but not
        # at 5, independent of the directional min_samples argument. Dates span
        # two ISO weeks so the regime guardrail does not filter the bucket.
        cases = [
            _neutral_case(f"N{i}", industry="有色", excess=-0.15, signal_date=_spread(i))
            for i in range(4)
        ]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(
            db=db, config={"cross_symbol_miner_neutral_min_samples": 4}
        )
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)
        assert result["neutral_significant_buckets"] >= 1
        assert "neutral:industry=有色" in {s["payload"]["dimension"] for s in db._saved}

        db2 = _neutral_db(cases)
        miner2 = CrossSymbolPatternMiner(
            db=db2, config={"cross_symbol_miner_neutral_min_samples": 5}
        )
        result2 = await miner2.mine(lookback_days=30, min_samples=5, min_lift=0.15)
        assert result2["neutral_significant_buckets"] == 0

    @pytest.mark.asyncio
    async def test_single_period_regime_filtered(self):
        # 6 strong, perfectly consistent cases but ALL on one signal date: this
        # clears the excess/consistency bars yet is a one-off sector/market
        # episode, so the ISO-week regime guardrail must drop it.
        cases = [
            _neutral_case(f"R{i}", industry="地产", excess=-0.15, signal_date="2026-06-04")
            for i in range(6)
        ]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(db=db, config={})
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)

        assert result["neutral_significant_buckets"] == 0
        assert result.get("neutral_regime_filtered", 0) >= 1
        assert not any(
            str(s["payload"]["dimension"]).startswith("neutral:") for s in db._saved
        )

    @pytest.mark.asyncio
    async def test_neutral_min_periods_config_honoured(self):
        # 6 consistent cases spanning exactly two ISO weeks: promoted at
        # min_periods=2 but filtered at min_periods=3.
        cases = [
            _neutral_case(f"W{i}", industry="地产", excess=-0.15, signal_date=_spread(i))
            for i in range(6)
        ]
        db = _neutral_db(cases)
        miner = CrossSymbolPatternMiner(
            db=db, config={"cross_symbol_miner_neutral_min_periods": 2}
        )
        result = await miner.mine(lookback_days=30, min_samples=5, min_lift=0.15)
        assert result["neutral_significant_buckets"] >= 1
        lesson = {s["payload"]["dimension"]: s for s in db._saved}["neutral:industry=地产"]
        assert lesson["payload"]["distinct_periods"] == 2

        db2 = _neutral_db(cases)
        miner2 = CrossSymbolPatternMiner(
            db=db2, config={"cross_symbol_miner_neutral_min_periods": 3}
        )
        result2 = await miner2.mine(lookback_days=30, min_samples=5, min_lift=0.15)
        assert result2["neutral_significant_buckets"] == 0
        assert result2.get("neutral_regime_filtered", 0) >= 1


def _governance_db(existing: list[dict]) -> MagicMock:
    db = MagicMock()
    db.list_strategy_lessons = MagicMock(return_value=existing)
    deactivated: list = []
    db.deactivate_strategy_lesson = MagicMock(side_effect=deactivated.append)
    db._deactivated = deactivated
    return db


class TestNeutralDeactivationGovernance:
    """Stale-lesson deactivation must respect the neutral namespace."""

    def test_stale_neutral_and_directional_deactivated(self):
        # Only the 地产 neutral bucket + BUY directional bucket recurred this
        # run; the stale 有色 neutral lesson must be deactivated.
        existing = [
            {"id": "d1", "payload": {"dimension": "final_decision=BUY"}},
            {"id": "n1", "payload": {"dimension": "neutral:industry=地产"}},
            {"id": "n2", "payload": {"dimension": "neutral:industry=有色"}},
        ]
        db = _governance_db(existing)
        miner = CrossSymbolPatternMiner(db=db, config={})
        result: dict = {}
        miner._deactivate_stale_lessons(
            {"final_decision=BUY", "neutral:industry=地产"}, result, include_neutral=True
        )
        assert db._deactivated == ["n2"]
        assert result["lessons_deactivated"] == 1

    def test_neutral_untouched_when_channel_disabled(self):
        # When the neutral channel did not run this call, neutral: lessons must
        # NOT be deactivated even though they are absent from active_dimensions
        # — a disabled channel should not silently retire them.
        existing = [
            {"id": "d1", "payload": {"dimension": "final_decision=BUY"}},
            {"id": "n1", "payload": {"dimension": "neutral:industry=地产"}},
        ]
        db = _governance_db(existing)
        miner = CrossSymbolPatternMiner(db=db, config={})
        result: dict = {}
        miner._deactivate_stale_lessons(
            {"final_decision=BUY"}, result, include_neutral=False
        )
        assert db._deactivated == []
        assert "lessons_deactivated" not in result
