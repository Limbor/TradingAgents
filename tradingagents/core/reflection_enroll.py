"""Shared reflection-case enrollment for skills.

Centralizes the ``Database.save_reflection_case`` call, ``case_id``
construction and scope determination (``decision_grade`` / ``candidate_pool`` /
``exploratory``) that both ``daily_pipeline`` and ``stock_analysis`` previously
performed inline.

Each skill keeps its own ``try``/``except`` + logging because their
error-handling shape differs: ``daily_pipeline`` loops over candidates and
counts created/failed, while ``stock_analysis`` enrolls a single case. This
helper therefore does *not* catch exceptions - callers wrap the call so their
skill-specific logging and counting are preserved exactly.

Scope determination is parameterized. The two skills historically used
different ``rating``/``decision`` -> scope mappings, so callers pass the value
sets explicitly to preserve their exact behavior (see A4 refactor notes).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def enroll_reflection_case(
    db,
    *,
    source_type: str,
    symbol: str,
    name: str | None,
    signal_date: str,
    rating_or_decision: Any,
    source_run_id: str,
    source_artifact_id: str = "",
    snapshot_payload: dict | None = None,
    horizon_days: int = 5,
    source: str | None = None,
    decision_grade_values: tuple[str, ...] = ("buy", "overweight", "sell"),
    candidate_pool_values: tuple[str, ...] = (),
    default_scope: str = "candidate_pool",
) -> None:
    """Enroll a single reflection case via ``db.save_reflection_case``.

    Constructs a deterministic ``case_id`` of the form
    ``f"{source}:{signal_date}:{symbol}"`` so re-running a skill on the same
    ``(date, symbol)`` replaces rather than duplicates (the table uses
    ``INSERT OR REPLACE``).

    Parameters
    ----------
    source_type:
        Value stored in ``reflection_cases.source_type`` (e.g.
        ``"system_signal"`` for daily_pipeline, ``"stock_analysis"`` for stock
        analysis).
    source:
        Prefix used in ``case_id``. Defaults to ``source_type``. The two differ
        for ``daily_pipeline`` (case_id prefix ``"daily_pipeline"`` vs
        ``source_type`` ``"system_signal"``); ``stock_analysis`` leaves this at
        ``None`` so the prefix falls back to ``source_type``.
    rating_or_decision:
        The rating (stock_analysis) or final_decision/signal (daily_pipeline)
        used to derive the reflection scope. Compared case-insensitively.
    decision_grade_values:
        Values mapping to ``decision_grade`` + ``eligible_for_strategy_learning``.
    candidate_pool_values:
        Values mapping to ``candidate_pool`` (not eligible). Empty by default.
    default_scope:
        Scope assigned when ``rating_or_decision`` matches neither set (not
        eligible). ``daily_pipeline`` uses ``"exploratory"``; ``stock_analysis``
        uses ``"candidate_pool"``.

    Raises
    ------
    Exception
        Any error from ``db.save_reflection_case`` propagates so callers can
        log/ count it with their own wording.
    """
    value = str(rating_or_decision or "").lower()
    grade_set = {v.lower() for v in decision_grade_values}
    pool_set = {v.lower() for v in candidate_pool_values}
    if value in grade_set:
        scope, eligible = "decision_grade", True
    elif value in pool_set:
        scope, eligible = "candidate_pool", False
    else:
        scope, eligible = default_scope, False

    prefix = source if source is not None else source_type
    case_id = f"{prefix}:{signal_date or 'undated'}:{(symbol or 'unknown').upper()}"

    db.save_reflection_case(
        case_id=case_id,
        source_type=source_type,
        reflection_scope=scope,
        eligible_for_strategy_learning=eligible,
        symbol=symbol,
        name=name,
        signal_date=signal_date,
        horizon_days=horizon_days,
        source_run_id=source_run_id,
        source_artifact_id=source_artifact_id,
        snapshot_payload=snapshot_payload,
        status="pending",
    )
