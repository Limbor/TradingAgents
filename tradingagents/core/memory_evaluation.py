"""Isolated paired candidate replay: identical evidence with/without memory.

No enrichment, no account writes, no lesson promotion. Outcomes are scoring
labels only and are never passed to the reviewer. Signed excess is a directional
quality proxy, NOT a simulated portfolio return.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from tradingagents.core.candidate_review_runner import coerce_llm_review
from tradingagents.core.decision_support import evaluation_horizon
from tradingagents.core.strategy_memory import (
    load_strategy_lessons,
    memory_cutoff,
    select_strategy_lessons,
    validate_memory_usage,
)

_INPUT_FIELDS = {
    "symbol", "ts_code", "name", "industry", "board", "quant_score", "factor_scores",
    "data_coverage", "risk_flags", "gate_reasons", "quant_gate_reasons", "quant_decision",
    "momentum_20d", "avg_amount_20d", "max_drawdown_60d", "factor_details",
    "horizon_days",
}
_DIRECTIONS = {"strong_positive": 1, "positive": 1, "neutral": 0,
               "negative": -1, "strong_negative": -1}


async def evaluate_memory_pairs(
    samples: list[dict], db: Any, reviewer_factory: Callable, *, style: str = "medium_term",
    model_label: str = "unspecified", min_samples: int = 20,
    on_progress: Callable | None = None,
) -> dict:
    """Replay chronologically with separate reviewers and alternating arm order.

    Input snapshot_as_of must equal trade_date. The caller supplies a frozen
    signal-time snapshot, never a current candidate fetched during the replay.
    """
    rows, seen = [], set()
    for index, sample in enumerate(sorted(samples, key=lambda row: str(row.get("trade_date") or ""))):
        day = str(sample.get("trade_date") or "")
        if sample.get("snapshot_as_of") != day:
            raise ValueError("Each snapshot_as_of must equal its trade_date")
        candidate = {key: value for key, value in (sample.get("candidate") or {}).items() if key in _INPUT_FIELDS}
        if not candidate.get("symbol") and not candidate.get("ts_code"):
            raise ValueError("Each replay snapshot requires an explicit symbol")
        identity = (day, str(candidate.get("symbol") or candidate.get("ts_code")))
        if identity in seen:
            raise ValueError("Duplicate symbol/date replay sample")
        seen.add(identity)
        outcome = sample.get("excess_return")
        if isinstance(outcome, bool) or not isinstance(outcome, (int, float)) or not math.isfinite(outcome):
            raise ValueError("Each replay sample requires a finite realized excess_return")
        cutoff = sample.get("snapshot_cutoff") or day
        cutoff_time, day_end = memory_cutoff(cutoff), memory_cutoff(day)
        if cutoff_time is None or day_end is None or cutoff_time > day_end:
            raise ValueError("snapshot_cutoff must not exceed the signal date")
        lessons = select_strategy_lessons(load_strategy_lessons(db, cutoff),
                                          {**candidate, "style": style,
                                           "horizon_days": candidate.get("horizon_days") or evaluation_horizon(style)}, as_of_date=cutoff)
        if not lessons:
            continue  # Do not pay for identical arms without applicable memory.
        predictions = {}
        arms = ("without_memory", "with_memory") if index % 2 == 0 else ("with_memory", "without_memory")
        for arm in arms:
            injected = lessons if arm == "with_memory" else []
            reviewer = reviewer_factory(day, [])
            if reviewer is None:
                raise RuntimeError("Replay reviewer unavailable; no comparison report produced")
            # Explicit empty memory overrides the reviewer's own pool in control.
            review = coerce_llm_review(await reviewer.review(deepcopy(candidate), strategy_lessons=deepcopy(injected)))
            direction = _DIRECTIONS[review.llm_view]
            predictions[arm] = {
                "view": review.llm_view, "direction": direction, "score": review.llm_score,
                "reasoning": review.reasoning[:600],
                "correct": direction * outcome > 0 if direction and outcome else None,
                "signed_excess": direction * outcome,
                "usage": validate_memory_usage(review.memory_usage, injected),
            }
        rows.append({"symbol": candidate.get("symbol") or candidate.get("ts_code"), "trade_date": day,
                     "snapshot_cutoff": cutoff,
                     "signal_snapshot": deepcopy(candidate),
                     "excess_return": outcome, "memory_ids": [row["id"] for row in lessons],
                     "memory_snapshots": lessons, **predictions})
        if on_progress:
            on_progress(len(rows))
    relevant = [row for row in rows if row["memory_ids"]]
    arms_summary = {}
    for arm in ("without_memory", "with_memory"):
        predictions = [row[arm] for row in relevant]
        directional = [p for p in predictions if p["correct"] is not None]
        arms_summary[arm] = {
            "directional_count": len(directional),
            "coverage": len(directional) / len(relevant) if relevant else None,
            "hit_rate": sum(p["correct"] for p in directional) / len(directional) if directional else None,
            "mean_signed_excess": statistics.mean(p["signed_excess"] for p in predictions) if predictions else None,
        }
    deltas = [r["with_memory"]["signed_excess"] - r["without_memory"]["signed_excess"] for r in relevant]
    delta = statistics.mean(deltas) if deltas else None
    interval = None
    if len(deltas) >= 2:
        half = 1.96 * statistics.stdev(deltas) / math.sqrt(len(deltas))
        interval = [delta - half, delta + half]
    return {"kind": "memory_paired_evaluation", "model": model_label, "style": style,
            "total_pairs": len(rows), "memory_pairs": len(relevant), "min_samples": min_samples,
            "sufficient_samples": len(relevant) >= min_samples,
            "changed_directions": sum(r["with_memory"]["direction"] != r["without_memory"]["direction"] for r in relevant),
            "arms": arms_summary, "mean_delta_signed_excess": delta, "approximate_95pct_interval": interval,
            "rows": rows, "warnings": [
                "方向超额是判断质量代理指标，不是账户收益；未模拟成交、费用或仓位。",
                "区间为样本独立假设下的近似值；同日、同股相关性和模型随机性仍需重复或分组评测。",
                "无适用经验的样本不计入记忆效果比较；该报告不会自动批准经验或调整策略。",
            ]}
