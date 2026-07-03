"""Deterministic Quant x LLM signal fusion helpers (v2).

Changes from v1:
- ATR-adaptive entry/stop/target (falls back to fixed % when ATR unavailable)
- Risk flag severity grading (critical / moderate / info)
- catalyst_score bonus integrated into fusion formula
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


STYLE_ALPHA = {
    "short_term": 0.70,
    "medium_term": 0.55,
    "long_term": 0.35,
}

# Risk flag severity classification keywords
CRITICAL_FLAGS = {"st", "ST", "*ST", "退市预警", "暂停上市", "一字跌停", "违规处罚", "信披违规", "终止上市"}
MODERATE_FLAGS = {"大股东减持", "质押比例高", "商誉减值", "业绩预亏", "北向大幅减持", "高管减持", "股权冻结"}


@dataclass(frozen=True)
class LLMAssessment:
    """Structured LLM assessment used by the fusion layer."""

    llm_confidence: float | None = None
    risk_override: bool = False
    invalidates_quant: bool = False
    risk_flags: tuple[str, ...] = ()
    reasoning: str | None = None
    catalyst_score: float | None = None
    llm_view: str = "neutral"
    catalyst_strength: str = "speculative"
    risk_assessment: str = "moderate"


POSITIVE_VIEWS = {"positive", "strong_positive"}
NEGATIVE_VIEWS = {"negative", "strong_negative"}
CATALYST_BUY = {"confirmed", "likely"}
FINAL_DECISIONS = {"BUY", "WATCHLIST", "MONITOR", "HOLD_REVIEW", "SKIP"}


def fuse_candidate_signal(
    candidate: dict[str, Any],
    style: str,
    llm_assessment: LLMAssessment | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a state-machine decision pack for one candidate.

    The legacy score fields are kept for display/backwards compatibility, but
    the canonical action is ``final_decision``.
    """
    assessment = _coerce_assessment(llm_assessment)
    quant_score = _clamp(_float_or(candidate.get("quant_score") or candidate.get("score"), 0.0))
    tradability = candidate.get("tradability") if isinstance(candidate.get("tradability"), dict) else {}
    is_tradable = bool(tradability.get("is_tradable", True))
    risk_flags = _merge_flags(candidate.get("risk_flags"), assessment.risk_flags)
    risk_severity = _classify_risk_severity(risk_flags)

    if assessment.llm_confidence is None:
        quant_weight = 1.0
        llm_weight = 0.0
        llm_confidence = None
        final_score = quant_score
        fusion_mode = "quant_only"
    else:
        quant_weight = STYLE_ALPHA.get(style, STYLE_ALPHA["medium_term"])
        llm_weight = 1.0 - quant_weight
        llm_confidence = _clamp(assessment.llm_confidence)
        # catalyst bonus: high catalyst boosts score, low catalyst penalizes
        catalyst_bonus = _catalyst_bonus(assessment.catalyst_score)
        raw_score = quant_weight * quant_score + llm_weight * llm_confidence + catalyst_bonus
        final_score = raw_score
        fusion_mode = "quant_llm_fused"

    final_score = _clamp(final_score)
    quant_decision = _normalize_decision(candidate.get("quant_decision") or candidate.get("decision") or _signal_from_score(quant_score))
    decision_pack = decision_gate(
        candidate,
        style,
        assessment if llm_assessment is not None else None,
        display_score=final_score,
        risk_flags=risk_flags,
        risk_severity=risk_severity,
        quant_decision=quant_decision,
    )
    final_decision = decision_pack["final_decision"]

    return {
        "signal": final_decision,
        "final_decision": final_decision,
        "decision_stage": decision_pack["decision_stage"],
        "display_score": decision_pack["display_score"],
        "strength": round(final_score, 1),
        "final_score": round(final_score, 1),
        "quant_score": round(quant_score, 1),
        "quant_decision": quant_decision,
        "llm_confidence": round(llm_confidence, 1) if llm_confidence is not None else None,
        "llm_view": assessment.llm_view,
        "catalyst_strength": assessment.catalyst_strength,
        "risk_assessment": assessment.risk_assessment,
        "catalyst_score": round(assessment.catalyst_score, 1) if assessment.catalyst_score is not None else None,
        "alpha_weight": {
            "quant": round(quant_weight, 2),
            "llm": round(llm_weight, 2),
        },
        "fusion_mode": fusion_mode,
        "position_pct": decision_pack["position_pct"],
        "entry_zone": _entry_zone(candidate),
        "stop_loss": _stop_loss(candidate),
        "targets": _targets(candidate),
        "horizon": _horizon(style),
        "risk_level": _risk_level(risk_flags, final_score, risk_severity),
        "risk_severity": risk_severity,
        "risk_flags": risk_flags,
        "gate_reasons": decision_pack["gate_reasons"],
        "action_plan": decision_pack["action_plan"],
        "reasoning": assessment.reasoning,
    }


def decision_gate(
    candidate: dict[str, Any],
    style: str,
    llm_assessment: LLMAssessment | dict[str, Any] | None = None,
    *,
    display_score: float | None = None,
    risk_flags: list[str] | None = None,
    risk_severity: str | None = None,
    quant_decision: str | None = None,
) -> dict[str, Any]:
    """Gate quant candidates with optional LLM review into final decisions."""
    assessment = _coerce_assessment(llm_assessment)
    has_llm = llm_assessment is not None
    quant_score = _clamp(_float_or(candidate.get("quant_score") or candidate.get("score"), 0.0))
    score = _clamp(display_score if display_score is not None else quant_score)
    quant_decision = _normalize_decision(quant_decision or candidate.get("quant_decision") or candidate.get("decision") or _signal_from_score(quant_score))
    tradability = candidate.get("tradability") if isinstance(candidate.get("tradability"), dict) else {}
    is_tradable = bool(tradability.get("is_tradable", True))
    risk_flags = risk_flags if risk_flags is not None else _merge_flags(candidate.get("risk_flags"), assessment.risk_flags)
    risk_severity = risk_severity or _classify_risk_severity(risk_flags)
    gate_reasons: list[str] = []

    final_decision = "MONITOR"
    if quant_decision == "SKIP":
        final_decision = "SKIP"
        gate_reasons.append("quant_decision_skip")
    elif not is_tradable:
        final_decision = "SKIP"
        gate_reasons.append("not_tradable")
    elif has_llm and assessment.invalidates_quant:
        final_decision = "SKIP"
        gate_reasons.append("llm_invalidates_quant")
    elif has_llm and assessment.risk_assessment == "critical":
        final_decision = "SKIP"
        gate_reasons.append("llm_critical_risk")
    elif risk_severity == "critical":
        final_decision = "SKIP"
        gate_reasons.append("critical_risk_severity")
    elif has_llm and assessment.risk_override:
        final_decision = "HOLD_REVIEW"
        gate_reasons.append("llm_risk_override")
    elif not has_llm:
        final_decision = "WATCHLIST" if quant_decision == "BUY" else quant_decision
        gate_reasons.append("llm_unavailable_or_not_run")
    elif quant_decision == "BUY" and assessment.llm_view in POSITIVE_VIEWS and assessment.catalyst_strength in CATALYST_BUY:
        final_decision = "BUY"
        gate_reasons.append("quant_buy_llm_confirms")
    elif quant_decision == "BUY" and assessment.llm_view == "neutral":
        final_decision = "WATCHLIST"
        gate_reasons.append("quant_buy_llm_neutral")
    elif quant_decision == "BUY" and assessment.llm_view in NEGATIVE_VIEWS:
        final_decision = "HOLD_REVIEW"
        gate_reasons.append("quant_buy_llm_negative")
    elif quant_decision == "WATCHLIST" and assessment.llm_view in POSITIVE_VIEWS:
        final_decision = "WATCHLIST"
        gate_reasons.append("watchlist_llm_positive")
    elif quant_decision == "MONITOR" and assessment.llm_view == "strong_positive" and assessment.catalyst_strength == "confirmed":
        final_decision = "WATCHLIST"
        gate_reasons.append("monitor_upgraded_by_confirmed_catalyst")
    else:
        final_decision = "MONITOR" if quant_decision in {"BUY", "WATCHLIST", "MONITOR"} else "SKIP"
        gate_reasons.append("default_gate")

    stage = "quant_llm_reviewed" if has_llm else "quant_only"
    return {
        "final_decision": final_decision,
        "decision_stage": stage,
        "display_score": round(score, 1),
        "position_pct": _position_pct(final_decision, score, risk_flags, risk_severity),
        "gate_reasons": gate_reasons,
        "action_plan": _action_plan(candidate, style, final_decision),
    }


def quant_evidence_markdown(candidate: dict[str, Any]) -> str:
    """Render a compact Quant Evidence block for Agent prompts."""
    fs = candidate.get("factor_scores") if isinstance(candidate.get("factor_scores"), dict) else {}
    trad = candidate.get("tradability") if isinstance(candidate.get("tradability"), dict) else {}
    key_metrics = candidate.get("key_metrics") if isinstance(candidate.get("key_metrics"), dict) else {}
    data_coverage = candidate.get("data_coverage") if isinstance(candidate.get("data_coverage"), dict) else {}
    explains = candidate.get("score_explain") or []
    risk_flags = candidate.get("risk_flags") or []
    lines = [
        "## Quant Evidence from StockManager",
        f"- symbol: {candidate.get('symbol') or candidate.get('ts_code')}",
        f"- name: {candidate.get('name', '')}",
        f"- quant_score: {candidate.get('quant_score', candidate.get('score'))} / 100",
        f"- quant_decision: {candidate.get('quant_decision', '')}",
        f"- quant_decision_reason: {candidate.get('quant_decision_reason', '')}",
    ]
    percentile = candidate.get("universe_percentile")
    if percentile is not None:
        lines.append(f"- universe_percentile: {percentile}")
    if fs:
        lines.append("- factor_scores: " + ", ".join(f"{k}={v}" for k, v in fs.items()))
    if key_metrics:
        lines.append("- key_metrics: " + ", ".join(f"{k}={v}" for k, v in key_metrics.items()))
    if data_coverage:
        lines.append("- data_coverage: " + ", ".join(f"{k}={v}" for k, v in data_coverage.items()))
    if candidate.get("quant_gate_reasons"):
        lines.append("- quant_gate_reasons: " + "; ".join(str(item) for item in candidate.get("quant_gate_reasons") or []))
    if trad:
        lines.append(
            "- tradability: "
            + ", ".join(f"{k}={v}" for k, v in trad.items() if k in {"is_tradable", "st_flag", "limit_status", "suspended"})
        )
    lines.append("- risk_flags: " + (", ".join(str(flag) for flag in risk_flags) if risk_flags else "none"))
    if explains:
        lines.append("- score_explain: " + "; ".join(str(item) for item in explains))
    lines.append("请验证该量化信号是否有新闻/行业/政策/基本面支撑，并指出可能推翻该信号的风险。")
    return "\n".join(lines)


def _coerce_assessment(value: LLMAssessment | dict[str, Any] | None) -> LLMAssessment:
    if isinstance(value, LLMAssessment):
        return value
    if not isinstance(value, dict):
        return LLMAssessment()
    llm_confidence = _float_or(value.get("llm_confidence"), None)
    catalyst_score = _float_or(value.get("catalyst_score"), None)
    return LLMAssessment(
        llm_confidence=llm_confidence,
        risk_override=bool(value.get("risk_override", False)),
        invalidates_quant=bool(value.get("invalidates_quant", False)),
        risk_flags=tuple(str(item) for item in value.get("risk_flags") or []),
        reasoning=str(value.get("reasoning")) if value.get("reasoning") else None,
        catalyst_score=catalyst_score,
        llm_view=_normalize_llm_view(value.get("llm_view"), llm_confidence),
        catalyst_strength=_normalize_catalyst_strength(value.get("catalyst_strength"), catalyst_score),
        risk_assessment=_normalize_risk_assessment(value.get("risk_assessment")),
    )


def _normalize_decision(value: Any) -> str:
    text = str(value or "").upper()
    if text == "HOLD":
        return "MONITOR"
    if text == "AVOID":
        return "SKIP"
    return text if text in FINAL_DECISIONS else "MONITOR"


def _normalize_llm_view(value: Any, confidence: float | None = None) -> str:
    text = str(value or "").lower()
    if text in {"strong_positive", "positive", "neutral", "negative", "strong_negative"}:
        return text
    if confidence is None:
        return "neutral"
    if confidence >= 85:
        return "strong_positive"
    if confidence >= 65:
        return "positive"
    if confidence < 35:
        return "negative"
    return "neutral"


def _normalize_catalyst_strength(value: Any, catalyst_score: float | None = None) -> str:
    text = str(value or "").lower()
    if text in {"confirmed", "likely", "speculative", "none"}:
        return text
    if catalyst_score is None:
        return "speculative"
    if catalyst_score >= 80:
        return "confirmed"
    if catalyst_score >= 60:
        return "likely"
    if catalyst_score <= 20:
        return "none"
    return "speculative"


def _normalize_risk_assessment(value: Any) -> str:
    text = str(value or "").lower()
    return text if text in {"low", "moderate", "high", "critical"} else "moderate"


def _signal_from_score(score: float) -> str:
    if score >= 75:
        return "BUY"
    if score >= 60:
        return "WATCHLIST"
    if score >= 45:
        return "HOLD"
    return "AVOID"


def _position_pct(signal: str, score: float, risk_flags: list[str], risk_severity: str = "none") -> float:
    """Compute position sizing based on signal, score and risk severity."""
    if signal != "BUY":
        return 0.0
    base = 4.0 + max(0.0, score - 75.0) * 0.25
    # Severity-aware position sizing
    if risk_severity == "critical":
        base *= 0.0
    elif risk_severity == "moderate":
        base *= 0.5
    elif risk_severity == "info":
        base *= 0.8
    elif risk_flags:
        # Legacy fallback when severity is not classified
        base *= 0.65
    return round(min(base, 10.0), 1)


def _action_plan(candidate: dict[str, Any], style: str, final_decision: str) -> dict[str, Any]:
    if final_decision != "BUY":
        return {
            "entry_condition": "等待量化门控和 LLM 催化剂进一步确认",
            "position_pct": 0.0,
            "stop_loss": None,
            "take_profit": None,
        }
    return {
        "entry_condition": "按计划分批建仓，避免追高",
        "position_pct": _position_pct("BUY", _clamp(_float_or(candidate.get("final_score") or candidate.get("quant_score"), 0.0)), candidate.get("risk_flags") or [], _classify_risk_severity(candidate.get("risk_flags") or [])),
        "stop_loss": _stop_loss(candidate),
        "take_profit": _targets(candidate),
        "horizon": _horizon(style),
    }


def _entry_zone(candidate: dict[str, Any]) -> list[float] | None:
    """Compute entry zone using ATR when available, fixed % as fallback."""
    price = _latest_price(candidate)
    if price is None:
        return None
    atr = _get_atr(candidate)
    if atr and atr > 0:
        return [round(price - 0.5 * atr, 2), round(price + 0.3 * atr, 2)]
    return [round(price * 0.985, 2), round(price * 1.01, 2)]


def _stop_loss(candidate: dict[str, Any]) -> float | None:
    """Compute stop loss using 2x ATR when available, fixed -8% as fallback."""
    price = _latest_price(candidate)
    if price is None:
        return None
    atr = _get_atr(candidate)
    if atr and atr > 0:
        return round(price - 2.0 * atr, 2)
    return round(price * 0.92, 2)


def _targets(candidate: dict[str, Any]) -> list[float] | None:
    """Compute profit targets using ATR multiples when available."""
    price = _latest_price(candidate)
    if price is None:
        return None
    atr = _get_atr(candidate)
    if atr and atr > 0:
        return [round(price + 2.0 * atr, 2), round(price + 4.0 * atr, 2)]
    return [round(price * 1.08, 2), round(price * 1.16, 2)]


def _latest_price(candidate: dict[str, Any]) -> float | None:
    for container in (candidate, candidate.get("factor_snapshot") or {}):
        if isinstance(container, dict):
            value = container.get("latest_price") or container.get("close")
            if value is not None:
                return _float_or(value, None)
    return None


def _get_atr(candidate: dict[str, Any]) -> float | None:
    """Extract ATR from factor_snapshot if available."""
    snapshot = candidate.get("factor_snapshot") or {}
    for key in ("atr_20", "atr20", "atr", "ATR_20", "ATR"):
        val = snapshot.get(key)
        if val is not None:
            try:
                result = float(val)
                if result > 0:
                    return result
            except (TypeError, ValueError):
                pass
    return None


def _horizon(style: str) -> str:
    if style == "short_term":
        return "3-10个交易日"
    if style == "long_term":
        return "1-3个月"
    return "2-4周"


def _classify_risk_severity(flags: list[str]) -> str:
    """Classify risk flag severity: 'critical', 'moderate', 'info', or 'none'."""
    if not flags:
        return "none"
    for flag in flags:
        if any(kw in flag for kw in CRITICAL_FLAGS):
            return "critical"
    for flag in flags:
        if any(kw in flag for kw in MODERATE_FLAGS):
            return "moderate"
    return "info"


def _catalyst_bonus(catalyst_score: float | None) -> float:
    """Catalyst factor bonus: high catalyst +5, low catalyst -3, neutral 0."""
    if catalyst_score is None:
        return 0.0
    if catalyst_score >= 80:
        return 5.0
    if catalyst_score >= 65:
        return 2.0
    if catalyst_score <= 20:
        return -3.0
    if catalyst_score <= 35:
        return -1.0
    return 0.0


def _risk_level(risk_flags: list[str], score: float, risk_severity: str = "none") -> str:
    """Determine risk level incorporating severity grading."""
    if risk_severity == "critical":
        return "critical"
    if risk_severity == "moderate":
        return "high"
    if risk_flags or score < 60:
        return "high"
    if score < 75:
        return "medium"
    return "low"


def _merge_flags(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        for item in value or []:
            text = str(item)
            if text and text not in merged:
                merged.append(text)
    return merged


def _float_or(value: Any, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float | None) -> float:
    return max(0.0, min(100.0, float(value or 0.0)))


# --- Exports for testing ---
__all__ = [
    "fuse_candidate_signal",
    "decision_gate",
    "quant_evidence_markdown",
    "LLMAssessment",
    "STYLE_ALPHA",
    "CRITICAL_FLAGS",
    "MODERATE_FLAGS",
]
