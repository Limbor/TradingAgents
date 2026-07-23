"""Unit tests for the pure adaptive-alpha suggestion module."""

from tradingagents.core.adaptive_alpha import resolve_alpha_override, suggest_alpha

# ── sample gate (min_n) ──────────────────────────────────────────────────


def test_below_min_n_returns_prior_not_applicable():
    result = suggest_alpha(0.4, 0.1, n=4, prior=0.55)
    assert result["applicable"] is False
    assert result["suggested_alpha"] == 0.55
    assert result["static_alpha"] == 0.55
    assert result["alpha_data"] is None
    assert result["data_weight"] == 0.0
    assert result["delta"] == 0.0
    # inputs echoed for transparency
    assert result["quant_ic"] == 0.4
    assert result["llm_ic"] == 0.1
    assert result["n"] == 4


# ── relative RankIC split ────────────────────────────────────────────────


def test_relative_split_pulls_toward_quant():
    # quant IC dominates -> alpha_data close to 1, suggestion above prior
    result = suggest_alpha(0.6, 0.0, n=40, prior=0.55, k=20, max_data_weight=0.5)
    assert result["applicable"] is True
    assert result["alpha_data"] == 1.0  # 0.6 / (0.6 + 0) = 1.0
    # w = min(40/60, 0.5) = 0.5 -> blended = 0.5*1.0 + 0.5*0.55 = 0.775
    assert result["data_weight"] == 0.5
    assert result["suggested_alpha"] == 0.775
    assert result["delta"] == round(0.775 - 0.55, 3)


def test_relative_split_pulls_toward_llm():
    # llm IC dominates -> alpha_data below prior
    result = suggest_alpha(0.0, 0.6, n=40, prior=0.55)
    assert result["applicable"] is True
    assert result["alpha_data"] == 0.0
    # w = 0.5 -> blended = 0.5*0.0 + 0.5*0.55 = 0.275
    assert result["suggested_alpha"] == 0.275
    assert result["delta"] < 0


# ── shrinkage toward prior ───────────────────────────────────────────────


def test_small_n_shrinks_closer_to_prior_than_large_n():
    small = suggest_alpha(0.6, 0.0, n=10, prior=0.55)
    large = suggest_alpha(0.6, 0.0, n=200, prior=0.55)
    # both pull toward quant (alpha_data=1.0) but small n stays closer to prior
    assert abs(small["suggested_alpha"] - 0.55) < abs(large["suggested_alpha"] - 0.55)


def test_data_weight_capped_by_max_data_weight():
    result = suggest_alpha(0.6, 0.0, n=10_000, prior=0.55, max_data_weight=0.5)
    # n/(n+k) -> ~1.0 but capped at 0.5
    assert result["data_weight"] == 0.5


# ── no positive predictive power falls back to prior ─────────────────────


def test_both_ic_non_positive_falls_back_to_prior():
    result = suggest_alpha(-0.52, -0.1, n=40, prior=0.55)
    assert result["applicable"] is True
    assert result["alpha_data"] == 0.55  # falls back to prior
    assert result["suggested_alpha"] == 0.55
    assert result["delta"] == 0.0
    assert "无正向预测力" in result["reason"]


def test_none_ic_treated_as_zero():
    result = suggest_alpha(None, None, n=40, prior=0.55)
    assert result["alpha_data"] == 0.55
    assert result["suggested_alpha"] == 0.55


# ── clamp to safety band ─────────────────────────────────────────────────


def test_suggestion_clamped_into_band():
    # extreme quant dominance + high prior would exceed band high; band caps it
    result = suggest_alpha(1.0, 0.0, n=10_000, prior=0.9, band=(0.2, 0.8), max_data_weight=1.0)
    assert result["suggested_alpha"] <= 0.8


def test_prior_outside_band_is_clamped_when_not_applicable():
    result = suggest_alpha(0.4, 0.1, n=2, prior=0.95, band=(0.2, 0.8))
    assert result["applicable"] is False
    assert result["suggested_alpha"] == 0.8  # prior clamped into band
    assert result["static_alpha"] == 0.95


# ── resolve_alpha_override (production gate) ─────────────────────────────


def _scorecard(suggestion):
    return {"available": True, "alpha_suggestion": suggestion}


def test_resolve_override_applicable_style_match_returns_alpha():
    sc = _scorecard(
        {
            "applicable": True,
            "style": "short_term",
            "suggested_alpha": 0.452,
            "static_alpha": 0.7,
            "delta": -0.248,
            "n": 11,
        }
    )
    override, meta = resolve_alpha_override(sc, "short_term")
    assert override == 0.452
    assert meta["applied"] is True
    assert meta["style"] == "short_term"
    assert meta["suggested_alpha"] == 0.452
    assert meta["static_alpha"] == 0.7
    assert meta["delta"] == -0.248
    assert meta["n"] == 11


def test_resolve_override_not_applicable_returns_none():
    sc = _scorecard({"applicable": False, "style": "short_term", "n": 4})
    override, meta = resolve_alpha_override(sc, "short_term")
    assert override is None
    assert meta["applied"] is False
    assert meta["reason"] == "not_applicable"
    assert meta["n"] == 4


def test_resolve_override_style_mismatch_returns_none():
    sc = _scorecard(
        {"applicable": True, "style": "long_term", "suggested_alpha": 0.4}
    )
    override, meta = resolve_alpha_override(sc, "short_term")
    assert override is None
    assert meta["applied"] is False
    assert meta["reason"] == "style_mismatch"
    assert meta["suggestion_style"] == "long_term"
    assert meta["style"] == "short_term"


def test_resolve_override_missing_suggestion_returns_none():
    override, meta = resolve_alpha_override({"available": False}, "short_term")
    assert override is None
    assert meta["applied"] is False
    assert meta["reason"] == "no_suggestion"


def test_resolve_override_none_scorecard_returns_none():
    override, meta = resolve_alpha_override(None, "short_term")
    assert override is None
    assert meta["applied"] is False
    assert meta["reason"] == "no_suggestion"
