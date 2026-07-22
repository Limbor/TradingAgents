"""Read-only prediction-quality metrics for the reflection scorecard.

This module is intentionally pure: no DB, no network, no I/O. It turns a list
of reflected ``reflection_cases`` rows (already JSON-decoded) into an aggregate
"prediction scorecard" describing how well past signals played out.

It is a measurement layer only — it never changes decisions, fusion weights, or
gates. Callers (persistence / API) supply the rows and render the result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ── directional correctness ──────────────────────────────────────────────
# Mirrors ReflectionEngine.evaluate_accuracy so the scorecard's hit-rate uses
# the exact same BUY/SELL semantics. Neutral decisions (WATCHLIST/HOLD/MONITOR)
# return None and are excluded from the accuracy denominator.


def directional_correct(decision: str, actual_return: float) -> bool | None:
    """Return True/False for directional decisions, None for neutral ones."""
    d = (decision or "").upper()
    if "BUY" in d or "OVERWEIGHT" in d:
        return actual_return > 0
    if "SELL" in d or "AVOID" in d or "UNDERWEIGHT" in d:
        return actual_return <= 0
    return None


# ── Spearman rank correlation (RankIC) ───────────────────────────────────


def _rank_with_ties(values: list[float]) -> list[float]:
    """Average-rank the values (ties share the mean of their rank span)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman_rankic(pairs: list[tuple[float, float]]) -> float | None:
    """Spearman rank correlation between two series. None if n < 3.

    Returns 0.0 when either series has zero variance (all ties), since no
    monotonic relationship can be measured.
    """
    if pairs is None or len(pairs) < 3:
        return None
    xs = [float(p[0]) for p in pairs]
    ys = [float(p[1]) for p in pairs]
    rx = _rank_with_ties(xs)
    ry = _rank_with_ties(ys)
    n = len(pairs)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    var_x = sum((rx[i] - mean_x) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_y) ** 2 for i in range(n))
    if var_x <= 0 or var_y <= 0:
        return 0.0
    return round(cov / (var_x**0.5 * var_y**0.5), 4)


# ── feature extraction from a single reflected case ──────────────────────


def _pick_number(*containers: dict[str, Any], key: str) -> float | None:
    for container in containers:
        if not isinstance(container, dict):
            continue
        value = container.get(key)
        if value in (None, "", "N/A"):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _pick_str(*containers: dict[str, Any], key: str) -> str | None:
    for container in containers:
        if not isinstance(container, dict):
            continue
        value = container.get(key)
        if value in (None, ""):
            continue
        return str(value)
    return None


def extract_features(case: dict[str, Any]) -> dict[str, Any] | None:
    """Extract (features + realized outcome) from one reflected case.

    Returns None when the case has no realized ``actual_return`` (nothing to
    score yet). Feature lookup order: snapshot top-level -> snapshot.candidate,
    since ``quant_score``/``final_decision`` live at the top level while
    ``llm_confidence``/``fusion_mode`` typically live in the candidate subdict.
    """
    outcome = case.get("outcome_payload") if isinstance(case.get("outcome_payload"), dict) else {}
    actual_return = outcome.get("actual_return")
    if not isinstance(actual_return, (int, float)):
        return None

    snapshot = case.get("snapshot_payload") if isinstance(case.get("snapshot_payload"), dict) else {}
    candidate = snapshot.get("candidate") if isinstance(snapshot.get("candidate"), dict) else {}

    decision = _pick_str(snapshot, candidate, key="final_decision") or _pick_str(
        snapshot, candidate, key="signal"
    )
    excess = outcome.get("sector_excess_return")
    if not isinstance(excess, (int, float)):
        excess = outcome.get("excess_return")
    if not isinstance(excess, (int, float)):
        excess = None

    horizon = case.get("horizon_days") or outcome.get("horizon_days")
    try:
        horizon = int(horizon) if horizon is not None else None
    except (TypeError, ValueError):
        horizon = None

    return {
        "symbol": str(case.get("symbol") or ""),
        "decision": decision or "",
        "quant_score": _pick_number(snapshot, candidate, key="quant_score"),
        "llm_confidence": _pick_number(snapshot, candidate, key="llm_confidence"),
        "fusion_mode": _pick_str(snapshot, candidate, key="fusion_mode"),
        "actual_return": float(actual_return),
        "excess_return": float(excess) if excess is not None else None,
        "horizon_days": horizon,
    }


# ── aggregation helpers ──────────────────────────────────────────────────


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count / hit_rate / avg_return / avg_excess over a group of features."""
    n = len(rows)
    returns = [r["actual_return"] for r in rows]
    excesses = [r["excess_return"] for r in rows if r["excess_return"] is not None]
    directional = [
        c
        for r in rows
        if (c := directional_correct(r["decision"], r["actual_return"])) is not None
    ]
    hit_rate = round(sum(1 for c in directional if c) / len(directional), 4) if directional else None
    return {
        "count": n,
        "directional_count": len(directional),
        "hit_rate": hit_rate,
        "avg_return": round(sum(returns) / n, 4) if n else None,
        "avg_excess": round(sum(excesses) / len(excesses), 4) if excesses else None,
    }


def _score_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 45:
        return "0-45"
    if value < 60:
        return "45-60"
    if value < 75:
        return "60-75"
    return "75-100"


def _confidence_bucket(value: float | None) -> str:
    if value is None:
        return "none"  # quant_only rows have no LLM confidence
    if value < 50:
        return "<50"
    if value < 70:
        return "50-70"
    return "70-100"


def _bucketed(
    features: list[dict[str, Any]],
    key_fn,
    order: list[str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in features:
        groups.setdefault(key_fn(f), []).append(f)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label in order:
        if label in groups:
            result.append({"bucket": label, **_aggregate(groups[label])})
            seen.add(label)
    # any labels not covered by the fixed order (e.g. unexpected decisions)
    for label in sorted(groups):
        if label not in seen:
            result.append({"bucket": label, **_aggregate(groups[label])})
    return result


# ── top-level scorecard builder ──────────────────────────────────────────

DECISION_ORDER = ["BUY", "WATCHLIST", "MONITOR", "HOLD_REVIEW", "SKIP", ""]


def build_scorecard(
    cases: list[dict[str, Any]],
    *,
    lookback_days: int,
    min_samples: int = 5,
    alpha_prior: float | None = None,
    style: str | None = None,
) -> dict[str, Any]:
    """Aggregate reflected cases into a prediction-quality scorecard.

    Degrades to ``{"available": False, ...}`` when too few evaluated samples
    exist, so the UI can show a "not enough samples" notice instead of noise.

    When ``alpha_prior`` is provided (the static ``STYLE_ALPHA`` for the active
    style), an advisory ``alpha_suggestion`` block is added — a read-only
    recommendation that never changes production decisions on its own.
    """
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    features = [f for case in cases if (f := extract_features(case)) is not None]
    n = len(features)

    if n < min_samples:
        return {
            "available": False,
            "reason": f"评估样本不足：现有 {n} 条已反思样本，需 ≥{min_samples} 条。",
            "n_evaluated": n,
            "min_samples": min_samples,
            "lookback_days": lookback_days,
            "as_of": as_of,
        }

    quant_pairs = [
        (f["quant_score"], f["actual_return"]) for f in features if f["quant_score"] is not None
    ]
    llm_pairs = [
        (f["llm_confidence"], f["actual_return"])
        for f in features
        if f["llm_confidence"] is not None
    ]

    horizon_dist: dict[str, int] = {}
    for f in features:
        label = str(f["horizon_days"]) if f["horizon_days"] is not None else "unknown"
        horizon_dist[label] = horizon_dist.get(label, 0) + 1

    quant_only = [f for f in features if f["fusion_mode"] == "quant_only"]
    fused = [f for f in features if f["fusion_mode"] == "quant_llm_fused"]

    quant_ic = spearman_rankic(quant_pairs)
    llm_ic = spearman_rankic(llm_pairs)

    scorecard: dict[str, Any] = {
        "available": True,
        "n_evaluated": n,
        "lookback_days": lookback_days,
        "as_of": as_of,
        "overall": _aggregate(features),
        "rank_ic": {
            "quant_score": {
                "value": quant_ic,
                "n": len(quant_pairs),
            },
            "llm_confidence": {
                "value": llm_ic,
                "n": len(llm_pairs),
            },
        },
        "fusion_comparison": {
            "quant_only": {"bucket": "quant_only", **_aggregate(quant_only)} if quant_only else None,
            "quant_llm_fused": {"bucket": "quant_llm_fused", **_aggregate(fused)} if fused else None,
        },
        "buckets": {
            "quant_score": _bucketed(features, lambda f: _score_bucket(f["quant_score"]), ["0-45", "45-60", "60-75", "75-100", "unknown"]),
            "llm_confidence": _bucketed(features, lambda f: _confidence_bucket(f["llm_confidence"]), ["<50", "50-70", "70-100", "none"]),
            "decision": _bucketed(features, lambda f: (f["decision"] or "").upper(), DECISION_ORDER),
        },
        "horizon_distribution": horizon_dist,
    }

    if alpha_prior is not None:
        from tradingagents.core.adaptive_alpha import suggest_alpha

        quant_n = len(quant_pairs)
        llm_n = len(llm_pairs)
        effective_n = min(quant_n, llm_n) if quant_n and llm_n else quant_n or llm_n
        suggestion = suggest_alpha(quant_ic, llm_ic, effective_n, prior=alpha_prior)
        suggestion["style"] = style
        scorecard["alpha_suggestion"] = suggestion

    return scorecard
