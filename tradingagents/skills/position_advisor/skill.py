"""Deterministic advice for an existing portfolio position."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.persistence import Database
from tradingagents.core.trading_time import get_temporal_context
from tradingagents.dataflows.symbol_utils import detect_market
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress
from tradingagents.skills.risk_monitor.skill import RiskMonitorInput, _scan_risks


class PositionAdvisorInput(BaseModel):
    symbol: str = Field(description="Ticker of an existing holding")
    intent: Literal["review", "add", "reduce", "exit"] = "review"
    lookback_days: int = Field(default=30, ge=1, le=365)
    stop_loss_pct: float | None = Field(default=None, ge=1, le=50)
    max_position_pct: float | None = Field(default=None, ge=1, le=100)


class PositionAdvisorOutput(BaseModel):
    symbol: str
    action: Literal["ADD", "HOLD", "REDUCE", "EXIT"]
    confidence: float
    holding: dict[str, Any]
    metrics: dict[str, Any]
    risk: dict[str, Any]
    trading_plan: dict[str, Any] | None = None
    execution: dict[str, Any]
    reasons: list[str]
    warnings: list[str]


class PositionAdvisorSkill(BaseSkill):
    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="position_advisor",
            name="Position Advisor",
            description=(
                "Review an existing holding using cost, P&L, concentration, risk announcements, "
                "and an optional StockManager trading plan."
            ),
            version="1.0.0",
            triggers=["要不要卖", "卖出建议", "加仓建议", "减仓建议", "止损", "position advice"],
            icon="scale",
            category="portfolio",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return PositionAdvisorInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return PositionAdvisorOutput

    async def execute(
        self, params: BaseModel, config: dict[str, Any]
    ) -> AsyncIterator[SkillEvent]:
        input_params: PositionAdvisorInput = params
        db = config.get("db") or Database()
        symbol = input_params.symbol.upper().replace(".SS", ".SH")
        holding = db.get_holding(symbol)

        yield SkillEvent(
            event_type="skill_start",
            data={"skill_id": self.metadata.id, "symbol": symbol},
        )
        if holding is None:
            message = f"{symbol} 不在当前持仓中，请先添加持仓后再获取仓位建议。"
            yield SkillEvent(
                event_type="report_chunk",
                data={"section": "position_advice", "content": message, "is_final": True},
            )
            yield SkillEvent(
                event_type="skill_complete",
                data={"status": "error", "symbol": symbol, "error": "holding_not_found"},
            )
            return

        yield skill_progress(
            stage_id="position_metrics",
            stage_label="持仓成本与仓位计算",
            status="running",
            progress_pct=20,
        )
        holdings = db.list_holdings()
        metrics, warnings = _position_metrics(holding, holdings)
        market = detect_market(symbol)
        profile = db.get_user_profile()
        stop_loss_pct = input_params.stop_loss_pct or _default_stop_loss(profile)
        max_position_pct = input_params.max_position_pct or _default_max_position(profile)
        yield skill_progress(
            stage_id="position_metrics",
            stage_label="持仓成本与仓位计算",
            status="completed",
            detail=f"浮盈亏 {metrics['pnl_pct']:+.2f}% · 仓位 {metrics['position_pct']:.2f}%",
            progress_pct=45,
        )

        yield skill_progress(
            stage_id="risk_scan",
            stage_label="公告与风险检查",
            status="running",
            progress_pct=55,
        )
        risks, mcp_risk_used = await _scan_risks(
            [holding], RiskMonitorInput(lookback_days=input_params.lookback_days), config
        )
        risk = risks[0] if risks else {
            "symbol": symbol,
            "level": "unknown",
            "message": "Risk data unavailable.",
            "announcements": [],
        }
        yield skill_progress(
            stage_id="risk_scan",
            stage_label="公告与风险检查",
            status="completed",
            detail=f"风险等级 {risk.get('level', 'unknown')}",
            progress_pct=70,
        )

        temporal = get_temporal_context(config, market="cn_a")
        trading_plan = await _load_trading_plan(
            config, holding, temporal.market_asof_date, profile
        )
        if trading_plan is None:
            warnings.append("StockManager trading plan unavailable; advice uses local risk rules.")

        action, confidence, reasons = _decide(
            input_params.intent,
            metrics,
            risk,
            stop_loss_pct=stop_loss_pct,
            max_position_pct=max_position_pct,
            trading_plan=trading_plan,
        )
        data_quality = _data_quality(metrics, risk, trading_plan)
        if action in {"ADD", "HOLD"} or str(risk.get("level")) == "unknown":
            confidence = min(confidence, data_quality["confidence_cap"])
        if data_quality["score"] < 0.5:
            reasons.append("关键行情或风险数据不完整，建议仅作人工复核，不视为确认安全")
        execution = _execution_sizing(action, metrics, max_position_pct, market=market)
        if action in {"ADD", "REDUCE"} and not execution["executable"]:
            reasons.append(str(execution["message"]))
            action = "HOLD"
            confidence = min(confidence, 0.4)
            execution = _execution_sizing(action, metrics, max_position_pct, market=market)
        elif execution.get("effective_action") == "EXIT" and action == "REDUCE":
            action = "EXIT"
            reasons.append("按交易单位取整后只能整仓退出，已将减仓建议提升为 EXIT")
        result = {
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "holding": holding,
            "metrics": metrics,
            "risk": risk,
            "trading_plan": trading_plan,
            "execution": execution,
            "data_quality": data_quality,
            "reasons": reasons,
            "warnings": warnings,
            "risk_scan_source": "StockManager MCP" if mcp_risk_used else "degraded",
            "market_asof_date": temporal.market_asof_date,
            "decision_target_date": temporal.decision_target_date,
        }
        report = _render_report(result, stop_loss_pct, max_position_pct)
        artifact_id = save_skill_artifact(
            config,
            skill_id=self.metadata.id,
            artifact_type="position_advice",
            title=f"{holding.get('name') or symbol} 持仓建议",
            subtitle=f"{action} · 置信度 {confidence:.0%}",
            subject_type="stock",
            subject_id=symbol,
            subject_name=holding.get("name") or symbol,
            summary="；".join(reasons[:2]),
            content_markdown=report,
            payload=result,
            tags=["position_advisor", action.lower()],
        )
        from tradingagents.core.decision_audit import decision_id

        audit_id = decision_id(
            "position_advisor", temporal.decision_target_date, symbol,
            str(config.get("run_id") or artifact_id or ""),
        )
        db.upsert_decision_record(
            decision_id=audit_id,
            source_type="position_advisor",
            source_run_id=str(config.get("run_id") or ""),
            source_artifact_id=str(artifact_id or ""),
            symbol=symbol,
            name=holding.get("name"),
            decision_date=temporal.decision_target_date,
            decision=action,
            horizon_days=5,
            reference_price=metrics.get("current_price"),
            payload=result,
        )
        result["decision_id"] = audit_id
        yield SkillEvent(
            event_type="position_advice",
            data=result,
        )
        yield SkillEvent(
            event_type="report_chunk",
            data={"section": "position_advice", "content": report, "is_final": True},
        )
        yield skill_progress(
            stage_id="recommendation",
            stage_label="生成仓位建议",
            status="completed",
            detail=f"建议 {action} · 置信度 {confidence:.0%}",
            progress_pct=100,
        )
        yield SkillEvent(event_type="skill_complete", data={"status": "success", **result})

    async def cancel(self) -> None:
        return None


def _position_metrics(
    holding: dict[str, Any], holdings: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    quantity = float(holding.get("quantity") or 0)
    avg_cost = float(holding.get("avg_cost") or 0)
    current_raw = holding.get("current_price")
    current_price = float(current_raw if current_raw is not None else avg_cost)
    warnings: list[str] = []
    if current_raw is None:
        warnings.append("Current price missing; average cost is used as a neutral fallback.")
    cost_value = quantity * avg_cost
    market_value = quantity * current_price
    total_value = sum(
        float(item.get("quantity") or 0)
        * float(item.get("current_price") if item.get("current_price") is not None else item.get("avg_cost") or 0)
        for item in holdings
    )
    pnl = market_value - cost_value
    return {
        "quantity": quantity,
        "avg_cost": avg_cost,
        "current_price": current_price,
        "cost_value": round(cost_value, 2),
        "market_value": round(market_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round((pnl / cost_value * 100) if cost_value else 0.0, 2),
        "position_pct": round((market_value / total_value * 100) if total_value else 0.0, 2),
        "portfolio_market_value": round(total_value, 2),
        "other_holdings_value": round(max(total_value - market_value, 0.0), 2),
        "current_price_available": current_raw is not None,
    }, warnings


def _default_stop_loss(profile: dict[str, Any]) -> float:
    return {"low": 7.0, "moderate": 10.0, "high": 15.0}.get(
        str(profile.get("risk_tolerance")), 10.0
    )


def _default_max_position(profile: dict[str, Any]) -> float:
    return {"low": 20.0, "moderate": 30.0, "high": 40.0}.get(
        str(profile.get("risk_tolerance")), 30.0
    )


def _decide(
    intent: str,
    metrics: dict[str, float],
    risk: dict[str, Any],
    *,
    stop_loss_pct: float,
    max_position_pct: float,
    trading_plan: dict[str, Any] | None = None,
) -> tuple[str, float, list[str]]:
    level = str(risk.get("level") or "unknown").lower()
    pnl_pct = metrics["pnl_pct"]
    position_pct = metrics["position_pct"]
    plan_stop = _extract_plan_stop(trading_plan)
    if level in {"red", "critical"}:
        return "EXIT", 0.9, ["命中严重公告或合规风险", str(risk.get("message") or "")]
    if plan_stop is not None and metrics["current_price"] <= plan_stop:
        return "EXIT", 0.88, [f"现价 {metrics['current_price']:.2f} 已跌破交易计划止损价 {plan_stop:.2f}", "交易计划失效条件已经触发"]
    if pnl_pct <= -stop_loss_pct:
        return "REDUCE", 0.82, [f"浮亏 {pnl_pct:.2f}% 已超过 {stop_loss_pct:.1f}% 风险阈值", "优先控制单笔回撤"]
    if position_pct > max_position_pct:
        return "REDUCE", 0.78, [f"仓位 {position_pct:.2f}% 超过 {max_position_pct:.1f}% 集中度阈值", "降低组合集中风险"]
    if level in {"orange", "high"}:
        return "REDUCE", 0.72, ["存在需要跟踪的公告风险", str(risk.get("message") or "")]
    if intent == "add" and pnl_pct > 0 and position_pct < max_position_pct * 0.6 and level == "green":
        return "ADD", 0.62, ["当前持仓盈利且仓位低于集中度阈值", "未发现公告级风险；加仓仍需价格信号确认"]
    return "HOLD", 0.65, ["未触发止损、集中度或严重公告风险", "维持仓位并继续观察失效条件"]


def _extract_plan_stop(plan: dict[str, Any] | None) -> float | None:
    """Extract a stop price from common MCP response envelopes."""
    if not isinstance(plan, dict):
        return None
    for key in ("stop_loss", "stop_price", "trailing_stop", "chandelier_stop"):
        value = plan.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    for key in ("plan", "result", "data", "flight_plan"):
        nested = plan.get(key)
        if isinstance(nested, dict):
            found = _extract_plan_stop(nested)
            if found is not None:
                return found
    return None


def _execution_sizing(
    action: str,
    metrics: dict[str, Any],
    max_position_pct: float,
    *,
    market: str = "cn_a",
) -> dict[str, Any]:
    """Turn a directional recommendation into an auditable share delta."""
    quantity = metrics["quantity"]
    position_pct = metrics["position_pct"]
    current_price = float(metrics.get("current_price") or 0)
    other_value = metrics.get("other_holdings_value", 0.0)
    if action == "EXIT":
        target_pct = 0.0
        delta = -quantity
    elif action == "REDUCE":
        target_pct = min(max_position_pct, max(position_pct * 0.5, 0.0))
        target_value = _target_value_from_other(other_value, target_pct)
        target_quantity = target_value / current_price if current_price else quantity
        delta = target_quantity - quantity
    elif action == "ADD":
        target_pct = min(max_position_pct, max(position_pct * 1.25, position_pct + 1.0))
        target_value = _target_value_from_other(other_value, target_pct)
        target_quantity = target_value / current_price if current_price else quantity
        delta = target_quantity - quantity
    else:
        target_pct = position_pct
        delta = 0.0
    theoretical_delta = delta
    if market == "cn_a" and action in {"ADD", "REDUCE"}:
        lots = (
            int(abs(delta) // 100)
            if action == "ADD"
            else int(round(abs(delta) / 100.0))
        )
        delta = float(lots * 100) * (1 if delta > 0 else -1)
        if action == "REDUCE":
            delta = max(delta, -quantity)
    target_quantity = max(quantity + delta, 0.0)
    target_value = target_quantity * current_price
    resulting_total = other_value + target_value
    resulting_pct = (target_value / resulting_total * 100) if resulting_total else 0.0
    executable = action == "HOLD" or action == "EXIT" or abs(delta) > 0
    effective_action = "EXIT" if action == "REDUCE" and target_quantity <= 0 else action
    message = ""
    if not executable:
        message = "理论调整数量不足一个 A 股交易单位（100 股），不生成不可执行的调仓指令"
    return {
        "current_quantity": round(quantity, 4),
        "quantity_change": round(delta, 4),
        "theoretical_quantity_change": round(theoretical_delta, 4),
        "target_quantity": round(target_quantity, 4),
        "current_position_pct": round(position_pct, 2),
        "requested_target_position_pct": round(target_pct, 2),
        "target_position_pct": round(resulting_pct, 2),
        "lot_size": 100 if market == "cn_a" else 1,
        "executable": executable,
        "effective_action": effective_action,
        "message": message,
        "requires_user_confirmation": action != "HOLD",
    }


def _target_value_from_other(other_value: float, target_pct: float) -> float:
    ratio = min(max(target_pct / 100.0, 0.0), 0.999999)
    return ratio * other_value / (1.0 - ratio) if ratio else 0.0


def _data_quality(
    metrics: dict[str, Any], risk: dict[str, Any], trading_plan: dict[str, Any] | None
) -> dict[str, Any]:
    score = 1.0
    missing: list[str] = []
    if not metrics.get("current_price_available"):
        score -= 0.4
        missing.append("current_price")
    if str(risk.get("level") or "unknown").lower() == "unknown":
        score -= 0.3
        missing.append("risk_scan")
    if trading_plan is None:
        score -= 0.15
        missing.append("trading_plan")
    score = max(score, 0.0)
    return {
        "score": round(score, 2),
        "level": "high" if score >= 0.8 else "medium" if score >= 0.5 else "low",
        "missing": missing,
        "confidence_cap": round(max(0.3, score), 2),
    }


async def _load_trading_plan(
    config: dict[str, Any], holding: dict[str, Any], as_of_date: str, profile: dict[str, Any]
) -> dict[str, Any] | None:
    client = await get_mcp_client(config)
    if client is None or not hasattr(client, "generate_trading_plan"):
        return None
    position = {
        "symbol": holding.get("symbol"),
        "quantity": float(holding.get("quantity") or 0),
        "avg_cost": float(holding.get("avg_cost") or 0),
        "current_price": holding.get("current_price"),
    }
    try:
        payload = await client.generate_trading_plan(
            strategy="position_review",
            config=json.dumps(
                {
                    "investment_style": profile.get("investment_style"),
                    "risk_tolerance": profile.get("risk_tolerance"),
                },
                ensure_ascii=False,
            ),
            as_of_date=as_of_date,
            positions=[position],
            cash=float(config.get("portfolio_cash") or 0),
        )
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _render_report(result: dict[str, Any], stop_loss_pct: float, max_position_pct: float) -> str:
    metrics = result["metrics"]
    risk = result["risk"]
    execution = result["execution"]
    data_quality = result["data_quality"]
    lines = [
        "## 持仓建议",
        "",
        f"- 标的：**{result['symbol']}**",
        f"- 建议：**{result['action']}**（置信度 {result['confidence']:.0%}）",
        f"- 浮盈亏：**{metrics['pnl_pct']:+.2f}%**",
        f"- 组合占比：**{metrics['position_pct']:.2f}%**",
        f"- 风险等级：**{risk.get('level', 'unknown')}**",
        f"- 数据日期：**{result['market_asof_date']}**",
        f"- 数据质量：**{data_quality['level']}**（{data_quality['score']:.0%}）",
        f"- 建议数量变化：**{execution['quantity_change']:+g} 股**（目标 {execution['target_quantity']:g} 股）",
        f"- 目标仓位：**{execution['target_position_pct']:.2f}%**",
        "",
        "### 依据",
        *[f"- {reason}" for reason in result["reasons"] if reason],
        "",
        "### 风险边界",
        f"- 本次止损阈值：{stop_loss_pct:.1f}%",
        f"- 单票集中度阈值：{max_position_pct:.1f}%",
    ]
    if result["warnings"]:
        lines.extend(["", "### 数据提示", *[f"- {item}" for item in result["warnings"]]])
    return "\n".join(lines)


skill = PositionAdvisorSkill()
