"""Deterministic reconciliation between stock selection and deep analysis.

The two workflows intentionally answer slightly different questions, so they
must not be forced to emit identical labels.  This module maps both vocabularies
onto an entry-direction contract, identifies material changes, and records the
comparison in a machine-readable shape.  The deterministic result is the source
of truth; an LLM-provided explanation is evidence attached to that result.
"""

from __future__ import annotations

from typing import Any

_SELECTION_DIRECTION = {
    "BUY": "positive",
    "WATCHLIST": "neutral",
    "MONITOR": "neutral",
    "HOLD": "neutral",
    "HOLD_REVIEW": "neutral",
    "SKIP": "avoid",
    "AVOID": "avoid",
}

_RATING_DIRECTION = {
    "buy": "positive",
    "overweight": "positive",
    "hold": "neutral",
    "underweight": "avoid",
    "sell": "avoid",
}

_RATING_ACTION = {
    "buy": "ENTER",
    "overweight": "ADD",
    "hold": "HOLD",
    "underweight": "REDUCE",
    "sell": "EXIT",
}


def reconcile_selection_analysis(
    selection_context: dict[str, Any] | None,
    conclusion: dict[str, Any] | None,
    *,
    analysis_date: str | None = None,
) -> dict[str, Any]:
    """Compare a selection decision with a stock-analysis conclusion.

    ``requires_review`` is deliberately asymmetric: a deep pass may add useful
    information but it may not silently promote a non-BUY candidate or preserve
    a BUY after conviction has fallen.  Those changes must pass through an
    explicit review gate.
    """
    selection_context = selection_context or {}
    conclusion = conclusion or {}
    selection_decision = str(
        selection_context.get("final_decision")
        or selection_context.get("signal")
        or ""
    ).strip().upper()
    rating = str(conclusion.get("rating") or "").strip()
    selection_direction = _SELECTION_DIRECTION.get(selection_decision)
    analysis_direction = _RATING_DIRECTION.get(rating.lower())

    plan = conclusion.get("plan")
    plan_action = ""
    if isinstance(plan, dict):
        plan_action = str(plan.get("plan_action") or "").strip().upper()
    expected_action = _RATING_ACTION.get(rating.lower())
    plan_consistent = not plan_action or not expected_action or plan_action == expected_action

    if not selection_direction or not analysis_direction:
        status = "not_comparable"
        decision_changed = False
    elif selection_direction == analysis_direction:
        status = "aligned"
        decision_changed = False
    elif selection_direction == "positive" and analysis_direction == "neutral":
        status = "downgrade"
        decision_changed = True
    elif selection_direction == "neutral" and analysis_direction == "positive":
        status = "upgrade"
        decision_changed = True
    elif {selection_direction, analysis_direction} == {"positive", "avoid"}:
        status = "reversal"
        decision_changed = True
    elif (
        selection_direction == "avoid" and analysis_direction == "neutral"
    ) or (
        selection_direction == "neutral" and analysis_direction == "avoid"
    ):
        status = "compatible"
        decision_changed = False
    else:
        status = "not_comparable"
        decision_changed = False

    requires_review = bool(
        not plan_consistent
        or (selection_direction == "positive" and analysis_direction != "positive")
        or (selection_direction != "positive" and analysis_direction == "positive")
    )

    reported = conclusion.get("selection_reconciliation")
    reported = reported if isinstance(reported, dict) else {}
    explanation = str(reported.get("explanation") or "").strip()
    evidence = reported.get("new_evidence")
    new_evidence = [str(item).strip() for item in evidence or [] if str(item).strip()]

    return {
        "status": status,
        "selection_decision": selection_decision or None,
        "analysis_rating": rating or None,
        "selection_direction": selection_direction,
        "analysis_direction": analysis_direction,
        "decision_changed": decision_changed,
        "requires_review": requires_review,
        "explanation_required": requires_review,
        "explicit_explanation": bool(explanation),
        "explanation": explanation or None,
        "new_evidence": new_evidence,
        "selection_trade_date": (
            selection_context.get("trade_date")
            or selection_context.get("price_trade_date")
        ),
        "analysis_date": analysis_date,
        "plan_consistency": {
            "status": "consistent" if plan_consistent else "conflict",
            "rating_expected_action": expected_action,
            "plan_action": plan_action or None,
        },
        "model_reported_alignment": reported.get("alignment"),
    }
