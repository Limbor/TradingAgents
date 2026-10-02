"""Portfolio management skill.

Provides local holding CRUD plus simple performance and concentration
analysis. The skill is deliberately deterministic so Phase 2 remains usable
without market-data network access.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.persistence import Database
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress


class PortfolioInput(BaseModel):
    """Input parameters for portfolio management."""

    action: Literal["list", "upsert", "delete", "analyze"] = Field(
        default="analyze",
        description="Portfolio operation to perform",
    )
    symbol: str | None = Field(default=None, description="Ticker to add/update/delete")
    quantity: float | None = Field(default=None, ge=0, description="Share quantity")
    avg_cost: float | None = Field(default=None, ge=0, description="Average cost")
    current_price: float | None = Field(default=None, ge=0, description="Current price")
    notes: str | None = Field(default=None, description="Optional holding notes")

    @model_validator(mode="after")
    def _validate_action_requirements(self):
        if self.action in {"upsert", "delete"} and not self.symbol:
            raise ValueError("symbol is required for upsert/delete")
        if self.action == "upsert" and (self.quantity is None or self.avg_cost is None):
            raise ValueError("quantity and avg_cost are required for upsert")
        return self


class PortfolioOutput(BaseModel):
    """Portfolio skill output."""

    action: str
    holdings: list[dict[str, Any]]
    summary: dict[str, Any]


class PortfolioManagementSkill(BaseSkill):
    """Manage local portfolio holdings and produce a concise risk summary."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="portfolio_management",
            name="Portfolio Management",
            description=(
                "Track holdings, update position cost, calculate P&L, "
                "and produce concentration/rebalance suggestions."
            ),
            version="1.0.0",
            triggers=["portfolio", "持仓", "仓位", "组合", "position", "holding", "rebalance"],
            icon="briefcase",
            category="portfolio",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return PortfolioInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return PortfolioOutput

    async def execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        input_params: PortfolioInput = params
        db = config.get("db") or Database()

        yield SkillEvent(
            event_type="skill_start",
            data={"skill_id": self.metadata.id, "action": input_params.action},
        )
        yield skill_progress(
            stage_id="prepare",
            stage_label="准备持仓任务",
            status="completed",
            detail=f"操作: {input_params.action}",
            progress_pct=10,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Portfolio Manager",
                "status": f"正在执行持仓任务：{input_params.action}",
            },
        )
        yield skill_progress(
            stage_id="portfolio_mutation",
            stage_label="持仓变更",
            status="running",
            detail=f"正在执行 {input_params.action}",
            agent="Portfolio Manager",
            progress_pct=35,
        )

        if input_params.action == "upsert":
            holding = db.upsert_holding(
                symbol=input_params.symbol or "",
                quantity=float(input_params.quantity or 0),
                avg_cost=float(input_params.avg_cost or 0),
                current_price=(
                    float(input_params.current_price)
                    if input_params.current_price is not None
                    else None
                ),
                notes=input_params.notes,
            )
            yield SkillEvent(
                event_type="portfolio_update",
                data={"operation": "upsert", "holding": holding},
            )
        elif input_params.action == "delete":
            deleted = db.delete_holding(input_params.symbol or "")
            yield SkillEvent(
                event_type="portfolio_update",
                data={"operation": "delete", "symbol": input_params.symbol, "deleted": deleted},
            )
        yield skill_progress(
            stage_id="portfolio_mutation",
            stage_label="持仓变更",
            status="completed",
            detail="持仓数据已同步",
            agent="Portfolio Manager",
            progress_pct=55,
        )

        holdings = db.list_holdings()
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Portfolio Manager",
                "status": f"正在计算 {len(holdings)} 个持仓的市值、盈亏和集中度",
            },
        )
        yield skill_progress(
            stage_id="portfolio_metrics",
            stage_label="组合统计",
            status="running",
            detail=f"正在计算 {len(holdings)} 个持仓",
            agent="Portfolio Manager",
            progress_pct=75,
        )
        summary = _build_summary(holdings)
        report = _render_report(holdings, summary)
        _save_portfolio_artifact(config, input_params, holdings, summary, report)

        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "portfolio_summary",
                "content": report,
                "is_final": True,
            },
        )
        yield skill_progress(
            stage_id="portfolio_metrics",
            stage_label="组合统计",
            status="completed",
            detail=f"未实现盈亏 {summary['unrealized_pnl']}，风险等级 {summary['risk_level']}",
            agent="Portfolio Manager",
            progress_pct=100,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "action": input_params.action,
                "holdings": holdings,
                "summary": summary,
            },
        )

    async def cancel(self) -> None:
        return None


def _build_summary(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    invested = 0.0
    market_value = 0.0
    enriched = []
    for item in holdings:
        quantity = float(item.get("quantity") or 0)
        avg_cost = float(item.get("avg_cost") or 0)
        current_price = item.get("current_price")
        price = float(current_price if current_price is not None else avg_cost)
        cost_value = quantity * avg_cost
        value = quantity * price
        invested += cost_value
        market_value += value
        enriched.append({**item, "market_value": value, "cost_value": cost_value})

    pnl = market_value - invested
    pnl_pct = (pnl / invested * 100) if invested else 0.0
    largest = max(enriched, key=lambda x: x["market_value"], default=None)
    largest_weight = (largest["market_value"] / market_value * 100) if largest and market_value else 0.0

    risk_level = "balanced"
    if largest_weight >= 45:
        risk_level = "concentrated"
    elif pnl_pct <= -10:
        risk_level = "drawdown_watch"

    return {
        "holding_count": len(holdings),
        "invested": round(invested, 2),
        "market_value": round(market_value, 2),
        "unrealized_pnl": round(pnl, 2),
        "unrealized_pnl_pct": round(pnl_pct, 2),
        "largest_position": largest.get("symbol") if largest else None,
        "largest_weight_pct": round(largest_weight, 2),
        "risk_level": risk_level,
    }


def _render_report(holdings: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    if not holdings:
        return (
            "## Portfolio Summary\n\n"
            "No holdings are currently tracked. Add positions to enable P&L, "
            "concentration, and rebalance analysis."
        )

    lines = [
        "## Portfolio Summary",
        "",
        f"- Holdings: **{summary['holding_count']}**",
        f"- Market value: **{summary['market_value']}**",
        f"- Unrealized P&L: **{summary['unrealized_pnl']} ({summary['unrealized_pnl_pct']}%)**",
        f"- Largest position: **{summary['largest_position']} ({summary['largest_weight_pct']}%)**",
        f"- Risk level: **{summary['risk_level']}**",
        "",
        "| Symbol | Quantity | Avg Cost | Current | Market Value |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in holdings:
        quantity = float(item.get("quantity") or 0)
        avg_cost = float(item.get("avg_cost") or 0)
        current = item.get("current_price")
        price = float(current if current is not None else avg_cost)
        lines.append(
            f"| {item['symbol']} | {quantity:g} | {avg_cost:.2f} | {price:.2f} | {quantity * price:.2f} |"
        )
    lines.extend(
        [
            "",
            "### Rebalance Notes",
            "",
            "- Keep single-name exposure below 40% unless there is a clear catalyst and liquidity support.",
            "- Update current prices before using this as an execution plan.",
            "- For A-shares, account for T+1 and daily limit constraints before reducing risk.",
        ]
    )
    return "\n".join(lines)


def _save_portfolio_artifact(
    config: dict[str, Any],
    input_params: PortfolioInput,
    holdings: list[dict[str, Any]],
    summary: dict[str, Any],
    report: str,
) -> None:
    # Holdings rows from db.list_holdings() have no name column; resolve it so
    # the Library snapshot can show 贵州茅台 alongside 600519.SH.
    try:
        from tradingagents.core.portfolio_prices import resolve_portfolio_name
        holdings_for_payload = [
            {**h, "name": resolve_portfolio_name(str(h.get("symbol", "")))}
            for h in holdings
        ]
    except Exception:
        holdings_for_payload = holdings
    save_skill_artifact(
        config,
        skill_id="portfolio_management",
        artifact_type="portfolio_report",
        title="组合持仓报告",
        subtitle=f"{input_params.action} · {summary.get('holding_count', 0)} 个持仓",
        subject_type="portfolio",
        subject_id="default",
        subject_name="当前持仓",
        status="success",
        summary=(
            f"市值 {summary.get('market_value', 0)}，"
            f"未实现盈亏 {summary.get('unrealized_pnl', 0)}，"
            f"风险等级 {summary.get('risk_level', '-')}"
        ),
        content_markdown=report,
        payload={"action": input_params.action, "holdings": holdings_for_payload, "summary": summary},
        tags=["portfolio", input_params.action],
    )


skill = PortfolioManagementSkill()
