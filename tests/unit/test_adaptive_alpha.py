"""Unit tests for the pure adaptive-alpha suggestion module."""

from tradingagents.core.adaptive_alpha import suggest_alpha

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
