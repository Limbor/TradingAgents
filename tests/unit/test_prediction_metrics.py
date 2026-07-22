"""Unit tests for the pure prediction-quality metrics module."""

from tradingagents.core.prediction_metrics import (
    build_scorecard,
    directional_correct,
    extract_features,
    spearman_rankic,
)

# ── directional_correct ──────────────────────────────────────────────────


def test_directional_correct_buy_and_sell():
    assert directional_correct("BUY", 0.05) is True
    assert directional_correct("BUY", -0.02) is False
    assert directional_correct("SELL", -0.03) is True
    assert directional_correct("AVOID", 0.01) is False


def test_directional_correct_neutral_returns_none():
    assert directional_correct("WATCHLIST", 0.05) is None
    assert directional_correct("HOLD", -0.05) is None
    assert directional_correct("", 0.0) is None


# ── spearman_rankic ──────────────────────────────────────────────────────


def test_spearman_perfect_positive():
    pairs = [(1.0, 10.0), (2.0, 20.0), (3.0, 30.0), (4.0, 40.0)]
    assert spearman_rankic(pairs) == 1.0


def test_spearman_perfect_negative():
    pairs = [(1.0, 40.0), (2.0, 30.0), (3.0, 20.0), (4.0, 10.0)]
    assert spearman_rankic(pairs) == -1.0


def test_spearman_handles_ties():
    # x has a tie on the first two entries; should still compute a value in range
    pairs = [(1.0, 5.0), (1.0, 6.0), (2.0, 7.0), (3.0, 4.0)]
    value = spearman_rankic(pairs)
    assert value is not None
    assert -1.0 <= value <= 1.0


def test_spearman_zero_variance_returns_zero():
    pairs = [(5.0, 1.0), (5.0, 2.0), (5.0, 3.0)]
    assert spearman_rankic(pairs) == 0.0


def test_spearman_too_few_samples_returns_none():
    assert spearman_rankic([(1.0, 2.0), (3.0, 4.0)]) is None
    assert spearman_rankic([]) is None


# ── extract_features ─────────────────────────────────────────────────────


def test_extract_features_prefers_top_level_then_candidate():
    case = {
        "symbol": "600000",
        "horizon_days": 5,
        "snapshot_payload": {
            "final_decision": "BUY",
            "quant_score": 72.0,
            "candidate": {
                "llm_confidence": 65.0,
                "fusion_mode": "quant_llm_fused",
                # top-level quant_score should win over this
                "quant_score": 10.0,
            },
        },
        "outcome_payload": {
            "actual_return": 0.08,
            "sector_excess_return": 0.03,
        },
    }
    feats = extract_features(case)
    assert feats is not None
    assert feats["decision"] == "BUY"
    assert feats["quant_score"] == 72.0  # top-level wins
    assert feats["llm_confidence"] == 65.0  # from candidate
    assert feats["fusion_mode"] == "quant_llm_fused"
    assert feats["actual_return"] == 0.08
    assert feats["excess_return"] == 0.03
    assert feats["horizon_days"] == 5


def test_extract_features_skips_case_without_actual_return():
    case = {
        "symbol": "600000",
        "snapshot_payload": {"final_decision": "BUY", "quant_score": 50.0},
        "outcome_payload": {},  # no actual_return
    }
    assert extract_features(case) is None


def test_extract_features_excess_fallback_chain():
    # No sector_excess_return -> falls back to excess_return
    case = {
        "symbol": "000001",
        "snapshot_payload": {"final_decision": "SELL"},
        "outcome_payload": {"actual_return": -0.02, "excess_return": -0.01},
    }
    feats = extract_features(case)
    assert feats is not None
    assert feats["excess_return"] == -0.01

    # Neither present -> None
    case2 = {
        "symbol": "000002",
        "snapshot_payload": {"final_decision": "SELL"},
        "outcome_payload": {"actual_return": -0.02},
    }
    feats2 = extract_features(case2)
    assert feats2 is not None
    assert feats2["excess_return"] is None


# ── build_scorecard ──────────────────────────────────────────────────────


def _case(symbol, decision, quant, conf, mode, ret, excess=None, horizon=5):
    return {
        "symbol": symbol,
        "horizon_days": horizon,
        "snapshot_payload": {
            "final_decision": decision,
            "quant_score": quant,
            "candidate": {"llm_confidence": conf, "fusion_mode": mode},
        },
        "outcome_payload": {
            "actual_return": ret,
            "sector_excess_return": excess,
        },
    }


def test_build_scorecard_degrades_below_min_samples():
    cases = [_case("A", "BUY", 70, 60, "quant_llm_fused", 0.05)]
    result = build_scorecard(cases, lookback_days=90, min_samples=5)
    assert result["available"] is False
    assert result["n_evaluated"] == 1
    assert result["min_samples"] == 5
    assert "reason" in result


def test_build_scorecard_aggregates_and_splits_fusion_modes():
    cases = [
        _case("A", "BUY", 80, 70, "quant_llm_fused", 0.05, 0.03),
        _case("B", "BUY", 76, 65, "quant_llm_fused", 0.02, 0.01),
        _case("C", "BUY", 50, None, "quant_only", -0.03, -0.02),
        _case("D", "SELL", 40, None, "quant_only", -0.01, -0.01),
        _case("E", "WATCHLIST", 62, 55, "quant_llm_fused", 0.04, 0.02),
    ]
    result = build_scorecard(cases, lookback_days=90, min_samples=5)
    assert result["available"] is True
    assert result["n_evaluated"] == 5

    overall = result["overall"]
    assert overall["count"] == 5
    # Directional decisions: A(BUY,+ ok), B(BUY,+ ok), C(BUY,- miss), D(SELL,- ok)
    # WATCHLIST(E) is neutral -> excluded
    assert overall["directional_count"] == 4
    assert overall["hit_rate"] == round(3 / 4, 4)

    fusion = result["fusion_comparison"]
    assert fusion["quant_only"]["count"] == 2
    assert fusion["quant_llm_fused"]["count"] == 3

    # RankIC present with sample counts
    assert result["rank_ic"]["quant_score"]["n"] == 5
    assert result["rank_ic"]["llm_confidence"]["n"] == 3  # only fused rows have conf


def test_build_scorecard_buckets_and_horizon_distribution():
    cases = [
        _case("A", "BUY", 80, 72, "quant_llm_fused", 0.05, 0.03, horizon=5),
        _case("B", "BUY", 70, 60, "quant_llm_fused", 0.02, 0.01, horizon=5),
        _case("C", "BUY", 55, 45, "quant_llm_fused", -0.03, -0.02, horizon=10),
        _case("D", "SELL", 40, 30, "quant_llm_fused", -0.01, -0.01, horizon=10),
        _case("E", "WATCHLIST", 30, 20, "quant_llm_fused", 0.04, 0.02, horizon=5),
    ]
    result = build_scorecard(cases, lookback_days=90, min_samples=5)

    quant_buckets = {b["bucket"]: b for b in result["buckets"]["quant_score"]}
    # 80 -> 75-100, 70 -> 60-75, 55 -> 45-60, 40 -> 0-45, 30 -> 0-45
    assert quant_buckets["75-100"]["count"] == 1
    assert quant_buckets["60-75"]["count"] == 1
    assert quant_buckets["45-60"]["count"] == 1
    assert quant_buckets["0-45"]["count"] == 2

    decision_buckets = {b["bucket"]: b for b in result["buckets"]["decision"]}
    assert decision_buckets["BUY"]["count"] == 3
    assert decision_buckets["SELL"]["count"] == 1
    assert decision_buckets["WATCHLIST"]["count"] == 1

    assert result["horizon_distribution"] == {"5": 3, "10": 2}


# ── build_scorecard alpha_suggestion ─────────────────────────────────────


def _fused_cases():
    return [
        _case("A", "BUY", 80, 70, "quant_llm_fused", 0.05, 0.03),
        _case("B", "BUY", 76, 65, "quant_llm_fused", 0.02, 0.01),
        _case("C", "BUY", 50, 40, "quant_llm_fused", -0.03, -0.02),
        _case("D", "SELL", 40, 30, "quant_llm_fused", -0.01, -0.01),
        _case("E", "WATCHLIST", 62, 55, "quant_llm_fused", 0.04, 0.02),
    ]


def test_build_scorecard_omits_alpha_suggestion_without_prior():
    result = build_scorecard(_fused_cases(), lookback_days=90, min_samples=5)
    assert "alpha_suggestion" not in result


def test_build_scorecard_includes_alpha_suggestion_with_prior():
    result = build_scorecard(
        _fused_cases(), lookback_days=90, min_samples=5, alpha_prior=0.55, style="medium_term"
    )
    suggestion = result["alpha_suggestion"]
    assert suggestion["static_alpha"] == 0.55
    assert suggestion["style"] == "medium_term"
    # only 5 fused rows -> effective_n below default min_n (8) -> not applicable
    assert suggestion["applicable"] is False
    assert suggestion["suggested_alpha"] == 0.55
    assert suggestion["n"] == 5
