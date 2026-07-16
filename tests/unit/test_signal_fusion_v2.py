"""Tests for signal_fusion v2 enhancements.

Covers:
- ATR-adaptive entry/stop/target
- ATR fallback to fixed percentages
- Risk flag severity classification
- Severity-aware position sizing
- catalyst_score bonus in fusion formula
- Concurrent LLM review integration
- Fusion formula consistency (quant_only unchanged)
"""

import asyncio

from tradingagents.core.llm_candidate_review import CandidateLLMReview
from tradingagents.core.signal_fusion import (
    _catalyst_bonus,
    _classify_risk_severity,
    _entry_zone,
    _get_atr,
    _position_pct,
    _risk_level,
    _stop_loss,
    _targets,
    decision_gate,
    fuse_candidate_signal,
)

# --- ATR Adaptive Tests ---


class TestATRAdaptive:
    def test_entry_zone_with_atr(self):
        """ATR=2.0, price=50 → entry = [49.0, 50.6]"""
        candidate = {
            "factor_snapshot": {"close": 50.0, "atr_20": 2.0},
        }
        result = _entry_zone(candidate)
        assert result == [49.0, 50.6]

    def test_stop_loss_with_atr(self):
        """ATR=2.0, price=50 → stop = 50 - 2*2 = 46.0"""
        candidate = {
            "factor_snapshot": {"close": 50.0, "atr_20": 2.0},
        }
        result = _stop_loss(candidate)
        assert result == 46.0

    def test_targets_with_atr(self):
        """ATR=2.0, price=50 → targets = [54.0, 58.0]"""
        candidate = {
            "factor_snapshot": {"close": 50.0, "atr_20": 2.0},
        }
        result = _targets(candidate)
        assert result == [54.0, 58.0]

    def test_entry_zone_fallback_no_atr(self):
        """Without ATR, falls back to fixed 0.985/1.01 multipliers."""
        candidate = {
            "factor_snapshot": {"close": 100.0},
        }
        result = _entry_zone(candidate)
        assert result == [98.5, 101.0]

    def test_stop_loss_fallback_no_atr(self):
        """Without ATR, falls back to fixed 0.92 multiplier."""
        candidate = {
            "factor_snapshot": {"close": 100.0},
        }
        result = _stop_loss(candidate)
        assert result == 92.0

    def test_targets_fallback_no_atr(self):
        """Without ATR, falls back to fixed 1.08/1.16 multipliers."""
        candidate = {
            "factor_snapshot": {"close": 100.0},
        }
        result = _targets(candidate)
        assert result == [108.0, 116.0]

    def test_get_atr_various_keys(self):
        """ATR can be extracted from multiple key names."""
        assert _get_atr({"factor_snapshot": {"atr_20": 1.5}}) == 1.5
        assert _get_atr({"factor_snapshot": {"atr20": 2.3}}) == 2.3
        assert _get_atr({"factor_snapshot": {"atr": 3.0}}) == 3.0
        assert _get_atr({"factor_snapshot": {"ATR_20": 4.0}}) == 4.0

    def test_get_atr_none_when_missing(self):
        assert _get_atr({}) is None
        assert _get_atr({"factor_snapshot": {}}) is None
        assert _get_atr({"factor_snapshot": {"atr_20": 0}}) is None
        assert _get_atr({"factor_snapshot": {"atr_20": "invalid"}}) is None

    def test_no_price_returns_none(self):
        candidate = {"factor_snapshot": {"atr_20": 2.0}}
        assert _entry_zone(candidate) is None
        assert _stop_loss(candidate) is None
        assert _targets(candidate) is None


# --- Risk Severity Classification Tests ---


class TestRiskSeverity:
    def test_critical_flags(self):
        assert _classify_risk_severity(["ST"]) == "critical"
        assert _classify_risk_severity(["退市预警"]) == "critical"
        assert _classify_risk_severity(["一字跌停"]) == "critical"
        assert _classify_risk_severity(["信披违规处罚"]) == "critical"

    def test_moderate_flags(self):
        assert _classify_risk_severity(["大股东减持"]) == "moderate"
        assert _classify_risk_severity(["质押比例高"]) == "moderate"
        assert _classify_risk_severity(["业绩预亏公告"]) == "moderate"

    def test_info_flags(self):
        assert _classify_risk_severity(["轻微波动"]) == "info"
        assert _classify_risk_severity(["成交量萎缩"]) == "info"

    def test_no_flags(self):
        assert _classify_risk_severity([]) == "none"

    def test_critical_overrides_moderate(self):
        """If both critical and moderate flags exist, severity is critical."""
        assert _classify_risk_severity(["大股东减持", "ST"]) == "critical"

    def test_position_pct_critical(self):
        """Critical risk → zero position."""
        result = _position_pct("BUY", 85.0, ["ST"], "critical")
        assert result == 0.0

    def test_position_pct_moderate(self):
        """Moderate risk → half position."""
        result = _position_pct("BUY", 85.0, ["大股东减持"], "moderate")
        base = 4.0 + (85.0 - 75.0) * 0.25  # 6.5
        expected = round(base * 0.5, 1)
        assert result == expected

    def test_position_pct_info(self):
        """Info risk → 80% position."""
        result = _position_pct("BUY", 80.0, ["轻微波动"], "info")
        base = 4.0 + (80.0 - 75.0) * 0.25  # 5.25
        expected = round(base * 0.8, 1)
        assert result == expected

    def test_risk_level_critical(self):
        assert _risk_level(["ST"], 90.0, "critical") == "critical"

    def test_risk_level_moderate(self):
        assert _risk_level(["大股东减持"], 80.0, "moderate") == "high"


# --- Catalyst Bonus Tests ---


class TestCatalystBonus:
    def test_high_catalyst(self):
        assert _catalyst_bonus(85) == 5.0
        assert _catalyst_bonus(80) == 5.0

    def test_medium_high_catalyst(self):
        assert _catalyst_bonus(65) == 2.0
        assert _catalyst_bonus(79) == 2.0

    def test_neutral_catalyst(self):
        assert _catalyst_bonus(50) == 0.0
        assert _catalyst_bonus(40) == 0.0
        assert _catalyst_bonus(36) == 0.0
        assert _catalyst_bonus(64) == 0.0

    def test_low_catalyst(self):
        assert _catalyst_bonus(35) == -1.0
        assert _catalyst_bonus(21) == -1.0

    def test_very_low_catalyst(self):
        assert _catalyst_bonus(20) == -3.0
        assert _catalyst_bonus(10) == -3.0

    def test_none_catalyst(self):
        assert _catalyst_bonus(None) == 0.0


# --- Fusion Formula Tests ---


class TestFusionFormula:
    def test_quant_only_unchanged(self):
        """quant_only keeps scores but no longer auto-promotes BUY without LLM."""
        candidate = {
            "quant_score": 82.0,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "medium_term")
        assert result["fusion_mode"] == "quant_only"
        assert result["final_score"] == 82.0
        assert result["final_decision"] == "WATCHLIST"
        assert result["decision_stage"] == "quant_only"
        assert result["position_pct"] == 0.0
        assert result["llm_confidence"] is None
        assert result["catalyst_score"] is None

    def test_fused_with_catalyst_bonus(self):
        """Fusion with high catalyst should add bonus."""
        candidate = {
            "quant_score": 70.0,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        assessment = {
            "llm_confidence": 75.0,
            "catalyst_score": 85.0,  # +5 bonus
            "llm_view": "positive",
            "catalyst_strength": "likely",
            "risk_override": False,
            "invalidates_quant": False,
            "risk_flags": [],
        }
        result = fuse_candidate_signal(candidate, "medium_term", assessment)
        # medium_term alpha = 0.55
        expected_raw = 0.55 * 70.0 + 0.45 * 75.0 + 5.0  # 38.5 + 33.75 + 5 = 77.25
        assert result["final_score"] == round(expected_raw, 1)
        assert result["catalyst_score"] == 85.0
        assert result["final_decision"] == "WATCHLIST"

    def test_fused_with_negative_catalyst(self):
        """Fusion with very low catalyst should penalize."""
        candidate = {
            "quant_score": 78.0,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        assessment = {
            "llm_confidence": 70.0,
            "catalyst_score": 15.0,  # -3 penalty
            "llm_view": "negative",
            "catalyst_strength": "none",
            "risk_override": False,
            "invalidates_quant": False,
            "risk_flags": [],
        }
        result = fuse_candidate_signal(candidate, "medium_term", assessment)
        # 0.55*78 + 0.45*70 - 3 = 42.9 + 31.5 - 3 = 71.4
        assert result["final_score"] == 71.4
        assert result["final_decision"] == "HOLD_REVIEW"

    def test_critical_risk_gates_buy(self):
        """Critical risk severity skips the candidate."""
        candidate = {
            "quant_score": 90.0,
            "tradability": {"is_tradable": True},
            "risk_flags": ["ST标记"],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "medium_term")
        assert result["final_decision"] == "SKIP"
        assert "critical_risk_severity" in result["gate_reasons"]
        assert result["risk_severity"] == "critical"

    def test_risk_severity_in_output(self):
        """Output should contain risk_severity field."""
        candidate = {
            "quant_score": 80.0,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "short_term")
        assert "risk_severity" in result
        assert result["risk_severity"] == "none"

    def test_catalyst_score_in_output(self):
        """Output should contain catalyst_score when provided."""
        candidate = {
            "quant_score": 80.0,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        assessment = {
            "llm_confidence": 70.0,
            "catalyst_score": 60.0,
            "risk_override": False,
            "invalidates_quant": False,
            "risk_flags": [],
        }
        result = fuse_candidate_signal(candidate, "medium_term", assessment)
        assert result["catalyst_score"] == 60.0


class TestDecisionStateMachine:
    def test_quant_buy_llm_positive_likely_buys(self):
        result = decision_gate(
            {"quant_score": 82, "tradability": {"is_tradable": True}},
            "medium_term",
            {"llm_view": "positive", "catalyst_strength": "likely", "risk_assessment": "moderate"},
            quant_decision="BUY",
            display_score=82,
        )
        assert result["final_decision"] == "BUY"
        assert result["position_pct"] > 0
        assert "quant_buy_llm_confirms" in result["gate_reasons"]

    def test_quant_buy_llm_neutral_watchlist(self):
        result = decision_gate(
            {"quant_score": 82, "tradability": {"is_tradable": True}},
            "medium_term",
            {"llm_view": "neutral", "catalyst_strength": "speculative"},
            quant_decision="BUY",
        )
        assert result["final_decision"] == "WATCHLIST"

    def test_quant_buy_llm_negative_hold_review(self):
        result = decision_gate(
            {"quant_score": 82, "tradability": {"is_tradable": True}},
            "medium_term",
            {"llm_view": "negative", "catalyst_strength": "none"},
            quant_decision="BUY",
        )
        assert result["final_decision"] == "HOLD_REVIEW"

    def test_invalidates_quant_skips(self):
        result = decision_gate(
            {"quant_score": 82, "tradability": {"is_tradable": True}},
            "medium_term",
            {"llm_view": "positive", "catalyst_strength": "confirmed", "invalidates_quant": True},
            quant_decision="BUY",
        )
        assert result["final_decision"] == "SKIP"

    def test_critical_llm_risk_skips(self):
        result = decision_gate(
            {"quant_score": 82, "tradability": {"is_tradable": True}},
            "medium_term",
            {"llm_view": "positive", "catalyst_strength": "confirmed", "risk_assessment": "critical"},
            quant_decision="BUY",
        )
        assert result["final_decision"] == "SKIP"

    def test_quant_skip_cannot_be_rescued_by_llm(self):
        result = decision_gate(
            {"quant_score": 40, "tradability": {"is_tradable": True}},
            "medium_term",
            {"llm_view": "strong_positive", "catalyst_strength": "confirmed"},
            quant_decision="SKIP",
        )
        assert result["final_decision"] == "SKIP"

    def test_llm_unavailable_does_not_auto_buy(self):
        result = decision_gate(
            {"quant_score": 90, "tradability": {"is_tradable": True}},
            "medium_term",
            None,
            quant_decision="BUY",
            display_score=90,
        )
        assert result["final_decision"] == "WATCHLIST"
        assert result["position_pct"] == 0.0
        assert result["decision_stage"] == "quant_only"


class TestCandidateLLMReviewSchema:
    def test_discrete_schema_accepts_valid_values(self):
        review = CandidateLLMReview(
            llm_view="strong_positive",
            catalyst_strength="confirmed",
            risk_assessment="low",
            llm_confidence=88,
            catalyst_score=82,
        )
        assert review.as_fusion_payload()["llm_view"] == "strong_positive"
        assert review.as_fusion_payload()["catalyst_strength"] == "confirmed"

    def test_invalid_enums_fall_back_to_safe_defaults(self):
        review = CandidateLLMReview.model_validate(
            {
                "llm_view": "unknown",
                "catalyst_strength": "maybe",
                "risk_assessment": "weird",
                "llm_confidence": 70,
                "catalyst_score": 50,
            }
        )
        assert review.llm_view == "neutral"
        assert review.catalyst_strength == "speculative"
        assert review.risk_assessment == "moderate"


# --- Concurrent Review Integration ---


class TestConcurrentReview:
    def test_concurrent_reviews_gather(self, monkeypatch, tmp_path):
        """Verify asyncio.gather based review works correctly with multiple candidates."""
        import importlib

        from tradingagents.core.persistence import Database
        from tradingagents.skills.daily_pipeline.skill import DailyPipelineInput, DailyPipelineSkill

        daily_pipeline_module = importlib.import_module("tradingagents.skills.daily_pipeline.skill")

        review_count = 0

        class FakeMCPClient:
            async def rank_factor_candidates(self, **kwargs):
                return {
                    "status": "success",
                    "rows": [
                        {"ts_code": "000001.SZ", "name": "平安银行", "quant_score": 80, "factor_scores": {}, "tradability": {"is_tradable": True}, "risk_flags": []},
                        {"ts_code": "000002.SZ", "name": "万科A", "quant_score": 76, "factor_scores": {}, "tradability": {"is_tradable": True}, "risk_flags": []},
                    ],
                }

        class FakeReviewer:
            async def review(self, candidate, **kwargs):
                nonlocal review_count
                review_count += 1
                await asyncio.sleep(0.01)
                return {
                    "llm_view": "positive",
                    "llm_confidence": 72,
                    "catalyst_score": 68,
                    "risk_override": False,
                    "invalidates_quant": False,
                    "key_catalysts": ["趋势确认"],
                    "key_risks": [],
                    "risk_flags": [],
                    "reasoning": "Test reasoning",
                }

        async def fake_get_mcp(config):
            return FakeMCPClient()

        monkeypatch.setattr(daily_pipeline_module, "get_mcp_client", fake_get_mcp)

        async def run():
            db = Database(tmp_path / "concurrent.db")
            skill = DailyPipelineSkill()
            events = [
                event
                async for event in skill.execute(
                    DailyPipelineInput(trade_date="2026-06-30", limit=2, candidate_limit=5),
                    {
                        "db": db,
                        "run_id": "test-concurrent",
                        "daily_pipeline_llm_reviewer": FakeReviewer(),
                        "daily_pipeline_llm_review_limit": 2,
                    },
                )
            ]
            complete = [e for e in events if e.event_type == "skill_complete"][-1]
            return complete

        complete = asyncio.run(run())
        assert complete.data["review_meta"]["reviewed"] == 2
        assert review_count == 2
        # Both candidates should be fused
        for c in complete.data["candidates"]:
            assert c["fusion_mode"] == "quant_llm_fused"
            assert c["catalyst_score"] == 68.0
