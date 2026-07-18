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
    # Walk-forward stability is a pure post-processing summary of the purged CV
    # (per-fold sharpe consistency / out-of-sample decay); it runs no extra
    # backtests and searches no parameters.
    walk_forward = summarize_walk_forward(cv) if isinstance(cv, dict) else {}
    # Execution slippage + ablation contribution are opt-in, best-effort MCP
    # calls that only fire when the caller supplies the required inputs (a trade
    # blotter / an explicitly declared ablation set). Neither sweeps parameters.
    execution_slippage = await analyze_execution_slippage(client, enriched, config)
    ablation_study = await run_declared_ablations(client, config)
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
        "walk_forward": walk_forward,
        "execution_slippage": execution_slippage,
        "ablation_study": ablation_study,
        "production_gate_passed": (
            not missing and not provenance_missing and lookahead_safe and survivorship_safe and
            costs_declared and cv_valid
        ),
    }
    return enriched


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _extract_fold_sharpes(cv: dict[str, Any]) -> list[float]:
    """Pull per-fold sharpe values from a purged-CV payload.

    StockManager implementations expose the per-split scores under a handful of
    key names / shapes; accept the common ones so the walk-forward summary works
    regardless of the exact serialization (and degrades to [] when absent).
    """
    for key in ("fold_sharpes", "split_sharpes", "oos_sharpes", "sharpes"):
        raw = cv.get(key)
        if isinstance(raw, list) and raw:
            vals = [f for f in (_as_float(item) for item in raw) if f is not None]
            if vals:
                return vals
    # Nested per-split records: [{"sharpe": ...}, ...] under "splits"/"folds".
    for key in ("splits", "folds", "per_split"):
        raw = cv.get(key)
        if isinstance(raw, list) and raw:
            vals: list[float] = []
            for item in raw:
                if isinstance(item, dict):
                    # `is not None` (not truthiness) so a genuine 0.0 fold is kept.
                    fold = _as_float(item.get("oos_sharpe"))
                    if fold is None:
                        fold = _as_float(item.get("sharpe"))
                    if fold is not None:
                        vals.append(fold)
            if vals:
                return vals
    return []


def summarize_walk_forward(cv: dict[str, Any]) -> dict[str, Any]:
    """Summarize walk-forward stability from the purged-CV result.

    Returns fold-consistency metrics (how many out-of-sample folds stayed
    positive, the weakest fold, and the in-sample→out-of-sample sharpe decay).
    ``available`` is False when the CV payload carries no per-fold detail, so
    callers can render a graceful "not enough folds" state.
    """
    if not isinstance(cv, dict) or cv.get("status") == "error":
        return {"available": False}
    fold_sharpes = _extract_fold_sharpes(cv)
    mean_sharpe = next(
        (v for v in (_as_float(cv.get(k)) for k in ("oos_sharpe", "mean_sharpe", "cv_sharpe", "median_sharpe", "sharpe")) if v is not None),
        None,
    )
    # `is not None` fallbacks: 0.0 is a valid sharpe and must not be dropped.
    is_sharpe = _as_float(cv.get("is_sharpe"))
    if is_sharpe is None:
        is_sharpe = _as_float(cv.get("in_sample_sharpe"))
    oos_sharpe = _as_float(cv.get("oos_sharpe"))
    if oos_sharpe is None:
        oos_sharpe = _as_float(cv.get("out_of_sample_sharpe"))
    summary: dict[str, Any] = {
        "available": bool(fold_sharpes),
        "n_folds": len(fold_sharpes),
        "fold_sharpes": [round(v, 4) for v in fold_sharpes],
        "mean_sharpe": round(mean_sharpe, 4) if mean_sharpe is not None else None,
    }
    if fold_sharpes:
        positive = [v for v in fold_sharpes if v > 0]
        summary["positive_fold_ratio"] = round(len(positive) / len(fold_sharpes), 4)
        summary["min_fold_sharpe"] = round(min(fold_sharpes), 4)
        summary["max_fold_sharpe"] = round(max(fold_sharpes), 4)
        # All folds must clear zero for the walk-forward to be "consistent";
        # this is informational only (does not gate production readiness).
        summary["consistent"] = all(v > 0 for v in fold_sharpes)
    if is_sharpe is not None and oos_sharpe is not None:
        summary["is_sharpe"] = round(is_sharpe, 4)
        summary["oos_sharpe"] = round(oos_sharpe, 4)
        summary["oos_decay"] = round(is_sharpe - oos_sharpe, 4)
    return summary


def _addon_result(result: Any, success_extra: dict[str, Any]) -> dict[str, Any]:
    """Normalize a best-effort add-on MCP result into an availability marker.

    A non-dict return (or a dict with ``status == "error"``) degrades to
    ``{"available": False, ...}`` without ever raising, so a misbehaving MCP
    response cannot break the surrounding audit. ``str(result)`` is used for the
    non-dict case because such a value has no ``.get`` to read an error from.
    """
    if not isinstance(result, dict):
        return {"available": False, "reason": "error", "error": str(result)}
    if result.get("status") == "error":
        return {"available": False, "reason": "error", "error": result.get("error")}
    return {"available": True, **success_extra, **result}


async def analyze_execution_slippage(
    client: Any, enriched: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Best-effort execution-slippage breakdown for the backtest's trade blotter.

    Only runs when the result carries a non-empty ``trades`` list and the MCP
    client exposes ``analyze_execution_slippage``. Failures are swallowed into an
    ``{"available": False, ...}`` marker so auditing never breaks on this add-on.
    """
    trades = enriched.get("trades")
    if not isinstance(trades, list) or not trades:
        return {"available": False, "reason": "no_trades"}
    method = getattr(client, "analyze_execution_slippage", None)
    if method is None:
        return {"available": False, "reason": "unsupported"}
    participation = config.get("participation_rates")
    try:
        result = await method(
            trades, participation_rates=participation if isinstance(participation, list) else None
        )
    except Exception as exc:  # best-effort add-on
        return {"available": False, "reason": "error", "error": str(exc)}
    return _addon_result(result, {"trade_count": len(trades)})


async def run_declared_ablations(client: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Best-effort ablation comparison against an EXPLICITLY declared set.

    This is not a parameter searcher: the caller must declare a fixed
    ``backtest_base_experiment`` plus a ``backtest_ablations`` list (each an
    explicit "turn this off" variant). We run those exact variants once and
    return the base-vs-ablation contribution table. When either input is
    missing the step is skipped.
    """
    base_experiment = config.get("backtest_base_experiment")
    ablations = config.get("backtest_ablations")
    if not isinstance(base_experiment, dict) or not base_experiment:
        return {"available": False, "reason": "no_base_experiment"}
    if not isinstance(ablations, list) or not ablations:
        return {"available": False, "reason": "no_ablations"}
    method = getattr(client, "run_ablation_study", None)
    if method is None:
        return {"available": False, "reason": "unsupported"}
    try:
        result = await method(base_experiment, ablations)
    except Exception as exc:  # best-effort add-on
        return {"available": False, "reason": "error", "error": str(exc)}
    return _addon_result(result, {"n_ablations": len(ablations)})
