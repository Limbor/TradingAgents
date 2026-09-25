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
        eligible.append({
            "snapshot": snapshot,
            "outcome": outcome,
            "return": raw_return,
            "directional_return": -raw_return if short_direction else raw_return,
            "source_type": str(case.get("source_type") or "unknown"),
            "symbol": str(case.get("symbol") or ""),
            "signal_date": str(case.get("signal_date") or ""),
            "horizon_days": case.get("horizon_days"),
        })

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        decision = str(item["snapshot"].get("final_decision") or "UNKNOWN").upper()
        grouped[decision].append(item)

    by_decision = {key: _metrics(rows, min_samples) for key, rows in sorted(grouped.items())}
    grouped_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        grouped_source[item["source_type"]].append(item)
    by_source = {
        key: _metrics(rows, min_samples)
        for key, rows in sorted(grouped_source.items())
        if key != "unknown"
    }
    overall = _metrics(eligible, min_samples)
    unique_signal_keys = {
        (
            item["source_type"],
            item["signal_date"],
            item["symbol"],
            str(item["snapshot"].get("final_decision") or "UNKNOWN").upper(),
            item["horizon_days"],
        )
        for item in eligible
    }
    unique_symbols = {item["symbol"] for item in eligible if item["symbol"]}
    unique_dates = {item["signal_date"] for item in eligible if item["signal_date"]}
    duplicate_signal_count = max(0, len(eligible) - len(unique_signal_keys))
    warnings: list[str] = []
    if overall["sample_count"] < min_samples:
        warnings.append(
            f"Only {overall['sample_count']} realized samples; require {min_samples} before strategy claims."
        )
    pending_ratio = 1.0 - (len(eligible) / len(cases)) if cases else 0.0
    if pending_ratio > 0.2:
        warnings.append(f"{pending_ratio:.1%} of loaded cases lack realized returns.")
    if duplicate_signal_count:
        warnings.append(
            f"{duplicate_signal_count} duplicated source/date/symbol/decision samples are correlated."
        )
    return {
        "overall": overall,
        "by_decision": by_decision,
        "by_source": by_source,
        "unique_signal_count": len(unique_signal_keys),
        "unique_symbol_count": len(unique_symbols),
        "unique_signal_date_count": len(unique_dates),
        "duplicate_signal_count": duplicate_signal_count,
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
