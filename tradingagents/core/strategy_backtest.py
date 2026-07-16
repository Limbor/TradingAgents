"""Validation gates shared by API and StrategyBacktest skill."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def backtest_request_id(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "backtest:" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


async def audit_backtest_result(client: Any, result: dict[str, Any],
                                config: dict[str, Any]) -> dict[str, Any]:
    performance = result.get("performance") if isinstance(result.get("performance"), dict) else {}
    enriched = {**result}
    for key in ("total_return", "max_drawdown", "sharpe", "win_rate", "turnover"):
        if key not in enriched and key in performance:
            enriched[key] = performance[key]
    curve = enriched.get("equity_curve") or enriched.get("curve") or []
    cv = None
    if isinstance(curve, list) and curve:
        cv = await client.compute_purged_cv_sharpe(
            curve, n_splits=5, purge_days=int(config.get("purge_days") or 10)
        )
    required = ("total_return", "max_drawdown", "sharpe", "win_rate", "turnover")
    missing = [key for key in required if not isinstance(enriched.get(key), (int, float))]
    provenance_missing = [key for key in ("source", "data_version") if not enriched.get(key)]
    lookahead_safe = enriched.get("lookahead_bias_check_passed") is True
    survivorship_safe = enriched.get("survivorship_bias_check_passed") is True
    costs_declared = all(
        isinstance(enriched.get(key, config.get(key)), (int, float))
        for key in ("transaction_cost_bps", "slippage_bps")
    )
    cv_valid = isinstance(cv, dict) and cv.get("status") != "error" and any(
        isinstance(cv.get(key), (int, float))
        for key in ("oos_sharpe", "mean_sharpe", "cv_sharpe", "median_sharpe", "sharpe")
    )
    enriched["validation"] = {
        "required_metrics_present": not missing,
        "missing_metrics": missing,
        "missing_provenance": provenance_missing,
        "lookahead_bias_check_passed": lookahead_safe,
        "survivorship_bias_check_passed": survivorship_safe,
        "costs_declared": costs_declared,
        "transaction_cost_bps": enriched.get("transaction_cost_bps", config.get("transaction_cost_bps")),
        "slippage_bps": enriched.get("slippage_bps", config.get("slippage_bps")),
        "purged_cv": cv or {},
        "production_gate_passed": (
            not missing and not provenance_missing and lookahead_safe and survivorship_safe and
            costs_declared and cv_valid
        ),
    }
    return enriched
