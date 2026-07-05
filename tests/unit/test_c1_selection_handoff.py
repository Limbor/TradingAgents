"""C-1 tests: selection→analysis handoff + structured trade plan.

Covers the three pieces that fix points ④ (zero handoff) and ⑤'s "plan
display" half:
- orchestrator merges selection_context into stock_analysis params
- PortfolioDecision.trade_plan render → parse round-trip
- _extract_conclusion surfaces a structured plan
- _format_selection_context injects the selection plan into agent context
"""

import asyncio

from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    TradeCondition,
    TradePlan,
    render_pm_decision,
)
from tradingagents.core.orchestrator import Orchestrator
from tradingagents.skills.daily_pipeline.skill import DailyPipelineSkill
from tradingagents.skills.daily_review.skill import DailyReviewSkill
from tradingagents.skills.market_scanner.skill import MarketScannerSkill
from tradingagents.skills.portfolio_management.skill import PortfolioManagementSkill
from tradingagents.skills.risk_monitor.skill import RiskMonitorSkill
from tradingagents.skills.registry import SkillRegistry
from tradingagents.skills.stock_analysis.skill import (
    StockAnalysisSkill,
    _extract_trade_plan,
    _format_selection_context,
)


def _registry():
    registry = SkillRegistry()
    registry.register(StockAnalysisSkill())
    registry.register(PortfolioManagementSkill())
    registry.register(MarketScannerSkill())
    registry.register(DailyPipelineSkill())
    registry.register(DailyReviewSkill())
    registry.register(RiskMonitorSkill())
    return registry


SELECTION = {
    "symbol": "600519.SH",
    "final_decision": "BUY",
    "display_score": 82.0,
    "entry_zone": [1480.0, 1500.0],
    "stop_loss": 1400.0,
    "targets": [1650.0, 1750.0],
    "action_plan": {"entry_condition": "分批建仓", "position_pct": 5.0},
    "reasoning": "质量分强且催化明确",
    "price_trade_date": "2026-07-04",
}


def test_orchestrator_passes_selection_context_to_stock_analysis():
    async def run():
        route = await Orchestrator(_registry()).route(
            "帮我分析 茅台",
            context={"selection_context": SELECTION},
        )
        assert route.skill is not None
        assert route.skill.metadata.id == "stock_analysis"
        assert route.params["ticker"] == "600519.SH"
        assert route.params["selection_context"] == SELECTION

    asyncio.run(run())


def test_orchestrator_does_not_attach_selection_context_to_scanner():
    async def run():
        route = await Orchestrator(_registry()).route(
            "选股",
            context={"selection_context": SELECTION},
        )
        assert route.skill is not None
        assert route.skill.metadata.id == "market_scanner"
        assert "selection_context" not in route.params

    asyncio.run(run())


def test_orchestrator_backward_compatible_without_context():
    async def run():
        route = await Orchestrator(_registry()).route("帮我分析 茅台")
        assert route.skill is not None
        assert route.params["ticker"] == "600519.SH"
        assert "selection_context" not in route.params

    asyncio.run(run())


def test_format_selection_context_injects_plan_and_reconcile_instruction():
    text = _format_selection_context(SELECTION)
    assert "Prior selection conclusion" in text
    assert "BUY" in text
    assert "1480.0" in text  # entry_zone
    assert "1400.0" in text  # stop_loss
    assert "质量分强且催化明确" in text  # reasoning
    assert "compare your analysis conclusion" in text  # reconcile instruction
    assert _format_selection_context(None) == ""


def test_portfolio_decision_trade_plan_render_parse_roundtrip():
    decision = PortfolioDecision(
        rating=PortfolioRating.BUY,
        executive_summary="分批建仓",
        investment_thesis="质量强+催化明确",
        price_target=12.0,
        time_horizon="2-4周",
        trade_plan=TradePlan(
            entry_zone=[10.0, 10.5],
            stop_loss=9.0,
            targets=[11.0, 12.0],
            position_pct=5.0,
            conditions=[
                TradeCondition(kind="full", source="MA金叉+现金流", description="5/20 日均线金叉且经营现金流连续两季为正"),
                TradeCondition(kind="stop", description="收盘跌破前低 9.00"),
            ],
        ),
    )
    md = render_pm_decision(decision)
    assert "**Trade Plan**" in md
    plan = _extract_trade_plan(md)
    assert plan is not None
    assert plan["entry_zone"] == [10.0, 10.5]
    assert plan["stop_loss"] == 9.0
    assert plan["targets"] == [11.0, 12.0]
    assert plan["position_pct"] == 5.0
    assert plan["conditions"][0]["kind"] == "full"
    assert plan["conditions"][0]["source"] == "MA金叉+现金流"
    assert plan["conditions"][1]["kind"] == "stop"
    assert "source" not in plan["conditions"][1]


def test_extract_trade_plan_none_when_no_section():
    assert _extract_trade_plan("just prose, no trade plan") is None
    assert _extract_trade_plan("") is None


def test_extract_conclusion_includes_plan():
    skill = StockAnalysisSkill()
    sections = {
        "final_trade_decision": (
            "**Rating**: Buy\n\n"
            "**Executive Summary**: 分批建仓\n\n"
            "**Investment Thesis**: 质量强+催化明确\n\n"
            "**Price Target**: 12.0\n\n"
            "**Trade Plan**\n"
            "- Entry Zone: [10.0, 10.5]\n"
            "- Stop Loss: 9.0\n"
            "- Targets: [11.0, 12.0]\n"
            "- Position Sizing: 5.0%\n"
            "- Condition [full] (MA金叉+现金流): 5/20 日均线金叉且经营现金流连续两季为正\n"
            "- Condition [stop]: 收盘跌破前低 9.00\n"
        )
    }
    conclusion = skill._extract_conclusion(sections, "600519.SH")
    assert conclusion["rating"] == "Buy"
    assert conclusion["plan"] is not None
    assert conclusion["plan"]["entry_zone"] == [10.0, 10.5]
    assert conclusion["plan"]["stop_loss"] == 9.0
    assert conclusion["plan"]["conditions"][0]["kind"] == "full"
    assert conclusion["symbol"] == "600519.SH"
