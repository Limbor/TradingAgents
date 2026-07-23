"""Shared LLM review runner for quant-ranked equity candidates.

Used by both ``daily_pipeline`` and ``market_scanner`` so that candidate LLM
conclusions (reasoning / key_catalysts / key_risks) are produced consistently.

The runner degrades *visibly*: when the reviewer cannot be built it tags every
candidate with ``decision_stage="llm_unavailable"`` and returns a warning
string, instead of silently dropping the LLM layer and leaving the frontend
with ``reasoning=None``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from pydantic import BaseModel

from tradingagents.core.candidate_enrichment import CandidateContext, enrich_candidates
from tradingagents.core.industry_taxonomy import normalize_industry
from tradingagents.core.llm_candidate_review import (
    CandidateLLMReview,
    build_candidate_reviewer,
)
from tradingagents.core.signal_fusion import fuse_candidate_signal
from tradingagents.skills._shared import candidate_rationale

logger = logging.getLogger(__name__)


async def apply_llm_reviews(
    candidates: list[dict[str, Any]],
    *,
    config: dict[str, Any],
    trade_date: str,
    style: str,
    reviewer: Any | None = None,
    review_limit: int | None = None,
    enabled: bool = True,
    strategy_lessons: list[dict[str, Any]] | None = None,
    enrich: bool = False,
    alpha_override: float | None = None,
    disabled_warning: str = "LLM review disabled by config.",
    unavailable_warning: str = "LLM reviewer unavailable; using quant-only fusion.",
) -> tuple[list[str], dict[str, Any]]:
    """Review the top quant candidates with the configured quick LLM.

    Each reviewed candidate is re-fused with the LLM assessment so that
    ``reasoning`` / ``key_catalysts`` / ``key_risks`` / ``llm_review`` are
    populated on the candidate dict. Candidates beyond ``review_limit`` are
    tagged ``llm_review_status="skipped_by_review_limit"`` and keep their
    quant-only fusion.

    Candidates ranked beyond ``review_limit`` that match an *active* strategy
    lesson are pulled into the review window (bounded by
    ``daily_pipeline_llm_review_lesson_extra``) so the reflection loop's lessons
    actually influence matching candidates regardless of quant rank.

    Returns ``(warnings, meta)``. ``meta`` always carries ``enabled`` and
    ``reviewed``; ``available`` is ``False`` when the reviewer could not be
    built (callers should surface ``warnings`` to the user so the degradation
    is visible, not silent).
    """
    if not candidates:
        return [], {"enabled": enabled, "reviewed": 0}
    if not enabled:
        return [disabled_warning], {"enabled": False, "reviewed": 0}

    if reviewer is None:
        reviewer = build_candidate_reviewer(
            config,
            style=style,
            trade_date=trade_date,
            strategy_lessons=strategy_lessons or [],
        )

    if reviewer is None:
        # Visible degradation: keep quant-only fusion but tag the stage so the
        # frontend can explain why there is no LLM reasoning.
        for candidate in candidates:
            candidate["decision_stage"] = "llm_unavailable"
            quant_decision = candidate.get("quant_decision")
            candidate["final_decision"] = (
                "WATCHLIST"
                if quant_decision == "BUY"
                else candidate.get("final_decision", candidate.get("signal", "MONITOR"))
            )
            candidate["signal"] = candidate["final_decision"]
            if candidate["final_decision"] != "BUY":
                candidate["position_pct"] = 0.0
        return [unavailable_warning], {"enabled": True, "available": False, "reviewed": 0}

    if review_limit is None:
        review_limit = min(5, len(candidates))
    review_limit = max(0, min(int(review_limit or 0), len(candidates)))

    lessons = strategy_lessons or []
    # Candidates ranked beyond review_limit but matching an ACTIVE strategy
    # lesson would otherwise be skipped before the review loop, so the lesson
    # would never be applied. Pull a bounded number of them into the review
    # window (rank order preserved) so the reflection loop actually bites.
    lesson_extra_cap = max(0, int(config.get("daily_pipeline_llm_review_lesson_extra", 0) or 0))
    forced: list[dict[str, Any]] = []
    if lessons and lesson_extra_cap > 0 and review_limit < len(candidates):
        for candidate in candidates[review_limit:]:
            if len(forced) >= lesson_extra_cap:
                break
            if candidate_lesson_hits(candidate, lessons):
                forced.append(candidate)
    review_set = candidates[:review_limit] + forced
    reviewed_ids = {id(c) for c in review_set}

    warnings: list[str] = []
    reviewed = 0

    # Enrich reviewed candidates with real-time data (news, announcements, flow).
    context_map: dict[str, CandidateContext] = {}
    if enrich and review_set:
        try:
            context_map = await enrich_candidates(
                review_set,
                trade_date,
                config,
            )
        except Exception as exc:
            logger.warning("Candidate enrichment failed; proceeding without context: %s", exc)

    async def _do_review(candidate: dict[str, Any]) -> tuple[dict[str, Any], Any | None, Exception | None]:
        try:
            symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "")
            ctx = context_map.get(symbol)
            # Pass context only if the reviewer supports it; degrade gracefully.
            if "context" in inspect.signature(reviewer.review).parameters:
                raw_review = await reviewer.review(candidate, context=ctx)
            else:
                raw_review = await reviewer.review(candidate)
            return candidate, raw_review, None
        except Exception as exc:
            return candidate, None, exc

    results = await asyncio.gather(*[_do_review(c) for c in review_set])

    for candidate, raw_review, exc in results:
        if exc is not None:
            symbol = candidate.get("symbol") or candidate.get("ts_code") or "unknown"
            logger.warning("LLM review failed for %s: %s", symbol, exc)
            warnings.append(f"LLM review failed for {symbol}; kept quant-only signal.")
            continue
        review = coerce_llm_review(raw_review)
        lesson_hits = candidate_lesson_hits(candidate, lessons)
        candidate["llm_review"] = review.model_dump()
        candidate["key_catalysts"] = review.key_catalysts
        candidate["key_risks"] = review.key_risks
        candidate["strategy_lesson_hits"] = lesson_hits
        if lesson_hits:
            candidate["lesson_adjustment_reason"] = "LLM review considered recent strategy reflection lessons."
        candidate.update(
            fuse_candidate_signal(
                candidate,
                style,
                review.as_fusion_payload(),
                alpha_override=alpha_override,
            )
        )
        candidate["rationale"] = candidate_rationale(candidate, include_llm=True)
        reviewed += 1

    for candidate in candidates:
        if id(candidate) not in reviewed_ids:
            candidate["llm_review_status"] = "skipped_by_review_limit"

    return warnings, {
        "enabled": True,
        "available": True,
        "reviewed": reviewed,
        "review_limit": review_limit,
        "lesson_forced": len(forced),
    }


def coerce_llm_review(value: Any) -> CandidateLLMReview:
    """Coerce a reviewer return value into a CandidateLLMReview."""
    if isinstance(value, CandidateLLMReview):
        return value
    if isinstance(value, dict):
        return CandidateLLMReview.model_validate(value)
    if isinstance(value, BaseModel):
        return CandidateLLMReview.model_validate(value.model_dump())
    raise TypeError(f"Unsupported LLM review payload: {type(value)!r}")


def candidate_lesson_hits(candidate: dict[str, Any], lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return strategy lessons whose scope matches this candidate (max 5)."""
    if not lessons:
        return []
    symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "")
    industry = str(candidate.get("industry") or "")
    # Industry-scope lessons are keyed by the coarse taxonomy group, so the
    # candidate's raw industry must be normalized the same way before an exact
    # comparison (otherwise e.g. target "地产" never matches raw "房地产").
    industry_group = normalize_industry(industry)
    board = str(candidate.get("board") or "")
    data_coverage = candidate.get("data_coverage") or {}
    missing_keys = {
        key for key, value in data_coverage.items()
        if str(value).lower() == "missing"
    } if isinstance(data_coverage, dict) else set()
    hits: list[dict[str, Any]] = []
    for lesson in lessons:
        scope = str(lesson.get("scope") or "global")
        target = str(lesson.get("target") or "")
        matched = scope == "global"
        matched = matched or (scope == "symbol" and target == symbol)
        matched = matched or (
            scope == "industry" and target and target in (industry, industry_group)
        )
        matched = matched or (scope == "board" and target and target == board)
        matched = matched or (scope == "factor" and target in missing_keys)
        if matched:
            hits.append(
                {
                    "id": lesson.get("id"),
                    "lesson_type": lesson.get("lesson_type"),
                    "scope": scope,
                    "target": target,
                    "finding": lesson.get("finding"),
                    "suggested_adjustment": lesson.get("suggested_adjustment"),
                    "confidence": lesson.get("confidence"),
                }
            )
    return hits[:5]


__all__ = ["apply_llm_reviews", "coerce_llm_review", "candidate_lesson_hits"]
