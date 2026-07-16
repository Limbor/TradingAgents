"""Historical validation for DailyPipeline decisions and reflection outcomes."""

from __future__ import annotations

from collections import defaultdict
from math import sqrt
from typing import Any


def validate_decision_history(cases: list[dict[str, Any]], min_samples: int = 20) -> dict[str, Any]:
    """Aggregate reflected cases without inventing significance from tiny samples."""
    eligible: list[dict[str, Any]] = []
    for case in cases:
        snapshot = case.get("snapshot_payload") if isinstance(case.get("snapshot_payload"), dict) else {}
        outcome = case.get("outcome_payload") if isinstance(case.get("outcome_payload"), dict) else {}
        actual_return = outcome.get("actual_return")
        if not isinstance(actual_return, (int, float)):
            continue
        decision = str(snapshot.get("final_decision") or "UNKNOWN").upper()
        if decision not in {"BUY", "OVERWEIGHT", "ADD", "SELL", "AVOID", "UNDERWEIGHT", "REDUCE", "EXIT"}:
            continue
        raw_return = float(actual_return)
        short_direction = decision in {"SELL", "AVOID", "UNDERWEIGHT", "REDUCE", "EXIT"}
        eligible.append({"snapshot": snapshot, "outcome": outcome, "return": raw_return,
                         "directional_return": -raw_return if short_direction else raw_return})

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        decision = str(item["snapshot"].get("final_decision") or "UNKNOWN").upper()
        grouped[decision].append(item)

    by_decision = {key: _metrics(rows, min_samples) for key, rows in sorted(grouped.items())}
    overall = _metrics(eligible, min_samples)
    warnings: list[str] = []
    if overall["sample_count"] < min_samples:
        warnings.append(
            f"Only {overall['sample_count']} realized samples; require {min_samples} before strategy claims."
        )
    pending_ratio = 1.0 - (len(eligible) / len(cases)) if cases else 0.0
    if pending_ratio > 0.2:
        warnings.append(f"{pending_ratio:.1%} of loaded cases lack realized returns.")
    return {
        "overall": overall,
        "by_decision": by_decision,
        "loaded_cases": len(cases),
        "realized_cases": len(eligible),
        "pending_ratio": round(pending_ratio, 4),
        "warnings": warnings,
        "strategy_claims_allowed": overall["statistically_usable"],
        "effectiveness_claim_allowed": overall["positive_edge_significant"],
    }


def _metrics(rows: list[dict[str, Any]], min_samples: int) -> dict[str, Any]:
    returns = [float(item["return"]) for item in rows]
    directional = [float(item.get("directional_return", item["return"])) for item in rows]
    wins = [value for value in directional if value > 0]
    losses = [value for value in directional if value < 0]
    count = len(returns)
    avg = sum(returns) / count if count else 0.0
    directional_avg = sum(directional) / count if count else 0.0
    variance = sum((value - directional_avg) ** 2 for value in directional) / (count - 1) if count > 1 else 0.0
    standard_error = sqrt(variance / count) if count else 0.0
    ci_low = directional_avg - 1.96 * standard_error
    ci_high = directional_avg + 1.96 * standard_error
    return {
        "sample_count": count,
        "win_rate": round(len(wins) / count, 4) if count else 0.0,
        "average_return": round(avg, 6),
        "average_directional_return": round(directional_avg, 6),
        "directional_return_ci95": [round(ci_low, 6), round(ci_high, 6)],
        "positive_edge_significant": count >= min_samples and ci_low > 0,
        "average_win": round(sum(wins) / len(wins), 6) if wins else 0.0,
        "average_loss": round(sum(losses) / len(losses), 6) if losses else 0.0,
        "statistically_usable": count >= min_samples,
    }
