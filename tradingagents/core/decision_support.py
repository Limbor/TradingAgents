"""Condition decisions and conservative, account-free outcome evaluation."""
from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class DecisionCondition(BaseModel):
    description: str = Field(min_length=1, max_length=600)
    source: str = Field(default="", max_length=160, description="Actual research evidence/indicator basis; never invent a source.")
    metric: Literal["close", "event", "fundamental", "manual"] = "manual"
    operator: Literal["gte", "lte", "between"] | None = None
    threshold: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    upper: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    confirmation: str = Field(default="收盘确认", max_length=100)
    price_basis: Literal["none", "qfq", "hfq", "unknown"] = "unknown"


class DecisionBrief(BaseModel):
    action_state: Literal["consider_entry", "wait_trigger", "avoid", "insufficient_evidence"]
    summary: str = Field(min_length=1, max_length=600)
    entry_conditions: list[DecisionCondition] = Field(default_factory=list, max_length=6)
    exit_conditions: list[DecisionCondition] = Field(default_factory=list, max_length=6)
    invalidation_conditions: list[DecisionCondition] = Field(default_factory=list, max_length=6)
    recheck_conditions: list[str] = Field(default_factory=list, max_length=5)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=8)


def decision_requested(goal: str) -> bool:
    """A conservative execution guard; the model still selects the skill."""
    return bool(re.search(
        r"(?:能否|能不能|可不可以|是否|适合|值得|还能|可以|要不要|该不该).{0,12}(?:买|卖|投资|入场|持有)|"
        r"(?:买|卖|投资|入场|持有).{0,12}(?:吗|么|合适|值得)|"
        r"(?:什么时候|何时|什么条件|买卖时机|买卖建议|交易决策|投资建议|进入和退出|退出条件|止损|止盈)", goal))


def evaluation_horizon(style: str | None, explicit: int | None = None) -> int:
    if explicit is not None:
        return max(1, min(int(explicit), 250))
    return {"short_term": 5, "medium_term": 60, "long_term": 120}.get(str(style), 60)


def build_decision_brief(conclusion: dict, *, template: str, as_of_date: str,
                         style: str, horizon_days: int, raw: dict | None = None,
                         info_cutoff: str | None = None) -> dict:
    """Use the primary structured decision; never parse prose into executable rules."""
    if template == "research":
        brief = DecisionBrief(action_state="insufficient_evidence",
            summary="本轮为专题研究，尚未执行完整交易裁决。",
            evidence_gaps=["尚未综合技术、基本面及风险形成条件决策"],
            recheck_conditions=["如需进入或退出判断，继续完整决策评估，可复用有效研究"])
    elif raw:
        try:
            brief = DecisionBrief.model_validate(raw)
        except (ValidationError, TypeError):
            brief = DecisionBrief(action_state="insufficient_evidence",
                summary="研究结论的行动条件未通过格式校验，需要补充确认。",
                evidence_gaps=["结构化行动条件不完整或价格阈值无效"])
    else:
        brief = DecisionBrief(action_state="insufficient_evidence",
            summary="研究已完成，但缺少经结构化确认的行动条件。",
            evidence_gaps=["进入、退出及失效条件未得到结构化确认"],
            recheck_conditions=["补充条件决策；不从报告文字猜测执行价位"])
    if brief.action_state == "consider_entry" and not (brief.exit_conditions or brief.invalidation_conditions):
        brief.evidence_gaps.append("尚未明确退出或判断失效条件")
    if brief.action_state == "consider_entry" and (not brief.entry_conditions or brief.evidence_gaps):
        brief.action_state = "wait_trigger" if brief.entry_conditions else "insufficient_evidence"
    return {**brief.model_dump(), "version": 1, "as_of_date": as_of_date,
            "info_cutoff": info_cutoff or as_of_date,
            "horizon": style, "horizon_days": horizon_days,
            "time_horizon": conclusion.get("time_horizon"), "account_free": True,
            "condition_semantics": "进入条件全部满足；退出或失效条件任一满足需重新评估",
            "price_basis": "none", "evaluation_basis": "收盘观察，次日开盘假设进入；非实际成交"}


def _test(condition: dict, row: dict) -> bool | None:
    if (condition.get("metric") != "close" or not condition.get("source") or
            condition.get("price_basis") != "none" or
            condition.get("confirmation") != "收盘确认"):
        return None
    values = (row.get("close"), condition.get("threshold"))
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return None
    close, threshold = values
    operator = condition.get("operator")
    if operator == "gte":
        return close >= threshold
    if operator == "lte":
        return close <= threshold
    upper = condition.get("upper")
    if operator == "between" and isinstance(upper, (int, float)) and not isinstance(upper, bool) and math.isfinite(upper) and upper >= threshold:
        return threshold <= close <= upper
    return None


def evaluate_decision_path(brief: dict, rows: list[dict], *, fee_bps: float = 10) -> dict:
    """Evaluate only explicit closing-price rules on unadjusted daily bars.

    Unknown event/fundamental conditions are never presumed true. A close
    signal enters on the next session's open. No same-day round trip, leverage
    or account sizing is simulated. This is a research proxy, not a fill model.
    """
    if isinstance(fee_bps, bool) or not math.isfinite(fee_bps) or not 0 <= fee_bps <= 1000:
        raise ValueError("fee_bps must be finite and between 0 and 1000")
    base = {"kind": "hypothetical_condition_evaluation", "fee_bps_per_side": fee_bps,
            "warnings": ["假设成交价格，不模拟涨跌停、停牌、滑点、流动性、分红或实际账户收益。"]}
    if brief.get("action_state") in {"avoid", "insufficient_evidence"}:
        return {**base, "status": "not_actionable", "was_correct": None}
    entries = brief.get("entry_conditions") or []
    if not entries or brief.get("price_basis") != "none":
        return {**base, "status": "unverifiable", "was_correct": None,
                "reason": "缺少明确进入条件或价格口径不一致"}
    exits = [*(brief.get("exit_conditions") or []), *(brief.get("invalidation_conditions") or [])]
    unknown = False
    signal_index = None
    cutoff_day = str(brief.get("info_cutoff") or brief.get("as_of_date") or "")[:10]
    for index, row in enumerate(rows[:-1]):
        if cutoff_day and str(rows[index + 1].get("trade_date") or "") <= cutoff_day:
            continue  # Never hypothesize an entry before the available information.
        states = [_test(c, row) for c in entries]
        if False in states:
            continue
        if None in states:
            unknown = True
            continue
        signal_index = index
        break
    if signal_index is None:
        last_states = [_test(c, rows[-1]) for c in entries] if rows else []
        if last_states and all(state is True for state in last_states):
            return {**base, "status": "awaiting_next_session", "was_correct": None}
        return {**base, "status": "unverifiable" if unknown or (None in last_states and False not in last_states) else "not_triggered",
                "was_correct": None}
    entry_index = signal_index + 1
    if entry_index == len(rows) - 1:
        return {**base, "status": "awaiting_exit_session", "was_correct": None,
                "reason": "窗口末日才假设进入；尚无符合 T+1 的退出交易日"}
    entry = rows[entry_index].get("open")
    if not isinstance(entry, (int, float)) or isinstance(entry, bool) or not math.isfinite(entry) or entry <= 0:
        return {**base, "status": "unverifiable", "was_correct": None, "reason": "缺少次日开盘价"}
    exit_index, exit_reason = len(rows) - 1, "horizon_end"
    exit_price = rows[-1].get("close")
    for index in range(entry_index, len(rows) - 1):
        states = [_test(c, rows[index]) for c in exits]
        if True in states:
            # Closing signals can only hypothetically exit at the NEXT open;
            # even an entry-day stop observation satisfies T+1 this way.
            exit_index, exit_reason = index + 1, "exit_or_invalidation"
            exit_price = rows[exit_index].get("open")
            break
        if None in states:
            return {**base, "status": "unverifiable", "was_correct": None,
                    "entry_date": rows[entry_index]["trade_date"], "reason": "退出条件含未核验的事件或基本面条件"}
    held_end = exit_index if exit_reason == "exit_or_invalidation" else exit_index + 1
    closes = [entry, *[r.get("close") for r in rows[entry_index:held_end]], exit_price]
    if not closes or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in closes):
        return {**base, "status": "unverifiable", "was_correct": None}
    gross = exit_price / entry - 1
    net = (exit_price * (1 - fee_bps / 10000)) / (entry * (1 + fee_bps / 10000)) - 1
    return {**base, "status": "triggered", "entry_date": rows[entry_index]["trade_date"],
            "signal_date": rows[signal_index]["trade_date"], "exit_date": rows[exit_index]["trade_date"],
            "entry_price": entry, "exit_price": exit_price, "exit_reason": exit_reason,
            "gross_return": round(gross, 6), "net_return": round(net, 6),
            "max_adverse_close_return": round(min(closes) / entry - 1, 6),
            "max_favorable_close_return": round(max(closes) / entry - 1, 6),
            "was_correct": None}
