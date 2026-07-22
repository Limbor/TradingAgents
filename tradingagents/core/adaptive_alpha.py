"""Adaptive alpha suggestion (read-only recommendation layer).

Turns measured predictive power (quant vs LLM RankIC from the prediction
scorecard) into a *suggested* fusion weight ``alpha`` (the quant weight), shown
alongside the current static ``STYLE_ALPHA`` prior.

This is advisory only: it never changes a production decision by itself. The
suggestion shrinks toward the static prior when samples are scarce, so with the
tiny/noisy samples typical early on it stays essentially equal to the prior.
"""

from __future__ import annotations


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def suggest_alpha(
    quant_ic: float | None,
    llm_ic: float | None,
    n: int,
    *,
    prior: float,
    k: int = 20,
    max_data_weight: float = 0.5,
    band: tuple[float, float] = (0.2, 0.8),
    min_n: int = 8,
) -> dict:
    """Suggest a quant weight (alpha) from relative RankIC, shrunk to a prior.

    Args:
        quant_ic: Spearman RankIC of quant_score vs realized return (or None).
        llm_ic: Spearman RankIC of llm_confidence vs realized return (or None).
        n: Effective sample size supporting the comparison (see build_scorecard;
            typically ``min(quant_ic_n, llm_ic_n)``).
        prior: The static style alpha (STYLE_ALPHA[style]) to shrink toward.
        k: Shrinkage half-saturation constant (larger => trusts data slower).
        max_data_weight: Hard cap on how much the data can pull away from prior.
        band: Safety band the final suggestion is clamped into.
        min_n: Below this sample size the data is ignored (weight 0) and the
            suggestion equals the prior; ``applicable`` is False.

    Returns a dict with the suggested alpha and all inputs for transparency.
    """
    low, high = band
    q = max(quant_ic or 0.0, 0.0)
    ll = max(llm_ic or 0.0, 0.0)

    if n < min_n:
        return {
            "suggested_alpha": round(_clamp(prior, low, high), 3),
            "static_alpha": round(prior, 3),
            "alpha_data": None,
            "data_weight": 0.0,
            "delta": 0.0,
            "quant_ic": quant_ic,
            "llm_ic": llm_ic,
            "n": n,
            "applicable": False,
            "reason": f"样本不足（有效对比样本 {n} < {min_n}），维持静态权重。",
        }

    if q + ll <= 0:
        # Neither signal shows positive predictive power — keep the prior.
        alpha_data = prior
        reason = "两路信号均无正向预测力，维持静态权重。"
    else:
        alpha_data = q / (q + ll)
        reason = "按 quant/LLM RankIC 相对占比推导，并向静态先验收缩。"

    w = min(n / (n + k), max_data_weight)
    blended = w * alpha_data + (1.0 - w) * prior
    suggested = _clamp(blended, low, high)

    return {
        "suggested_alpha": round(suggested, 3),
        "static_alpha": round(prior, 3),
        "alpha_data": round(alpha_data, 3),
        "data_weight": round(w, 3),
        "delta": round(suggested - prior, 3),
        "quant_ic": quant_ic,
        "llm_ic": llm_ic,
        "n": n,
        "applicable": True,
        "reason": reason,
    }
