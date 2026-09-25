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
    PlanAction,
    PortfolioDecision,
    PortfolioRating,
    SelectionReconciliation,
    TradeCondition,
    TradePlan,
    render_pm_decision,
)
from tradingagents.core.decision_reconciliation import reconcile_selection_analysis
from tradingagents.core.orchestrator import Orchestrator
from tradingagents.skills.daily_pipeline.skill import (
    DailyPipelineSkill,
    _apply_deep_analysis_safety_gate,
)
from tradingagents.skills.daily_review.skill import DailyReviewSkill
from tradingagents.skills.market_scanner.skill import MarketScannerSkill
from tradingagents.skills.portfolio_management.skill import PortfolioManagementSkill
from tradingagents.skills.registry import SkillRegistry
from tradingagents.skills.risk_monitor.skill import RiskMonitorSkill
from tradingagents.skills.stock_analysis.skill import (
    StockAnalysisSkill,
    _apply_holding_execution_constraints,
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
        assert "ticker_name" in route.params and route.params["ticker_name"]

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
        assert "ticker_name" in route.params and route.params["ticker_name"]
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


def test_extract_conclusion_includes_stock_name():
    skill = StockAnalysisSkill()
    conclusion = skill._extract_conclusion({"final_trade_decision": "评级 Buy"}, "600519.SH")
    assert conclusion["symbol"] == "600519.SH"
    assert "name" in conclusion and conclusion["name"]


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
    assert plan["plan_action"] == "ENTER"
    assert plan["action_zone"] == [10.0, 10.5]
    assert plan["entry_zone"] == [10.0, 10.5]
    assert plan["stop_loss"] == 9.0
    assert plan["targets"] == [11.0, 12.0]
    assert plan["position_pct"] == 5.0
    assert plan["conditions"][0]["kind"] == "full"
    assert plan["conditions"][0]["trigger_action"] == "ADD"
    assert plan["conditions"][0]["source"] == "MA金叉+现金流"
    assert plan["conditions"][1]["kind"] == "stop"
    assert "source" not in plan["conditions"][1]


def test_bearish_rating_derives_reduce_actions_for_legacy_plan():
    decision = PortfolioDecision(
        rating=PortfolioRating.UNDERWEIGHT,
        executive_summary="反弹减仓",
        investment_thesis="风险收益比转弱",
        trade_plan=TradePlan(
            entry_zone=[14.9, 15.1],
            stop_loss=15.35,
            targets=[14.28, 13.74],
            conditions=[
                TradeCondition(kind="entry", description="反弹至区间"),
                TradeCondition(kind="take_profit", description="跌破目标位"),
            ],
        ),
    )

    assert decision.trade_plan is not None
    assert decision.trade_plan.plan_action.value == "REDUCE"
    assert [condition.trigger_action.value for condition in decision.trade_plan.conditions] == [
        "REDUCE",
        "EXIT",
    ]
    markdown = render_pm_decision(decision)
    assert "- Action: REDUCE" in markdown
    assert "Trigger Action [REDUCE]" in markdown


def test_explicit_hold_action_is_not_overridden_by_rating():
    decision = PortfolioDecision(
        rating=PortfolioRating.SELL,
        executive_summary="等待确认",
        investment_thesis="当前不执行交易",
        trade_plan=TradePlan(plan_action="HOLD"),
    )

    assert decision.trade_plan is not None
    assert decision.trade_plan.plan_action.value == "HOLD"


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
    assert conclusion["target_price"] == 12.0
    assert conclusion["reasons"] == ["分批建仓", "质量强+催化明确"]
    assert conclusion["plan"] is not None
    assert conclusion["plan"]["entry_zone"] == [10.0, 10.5]
    assert conclusion["plan"]["stop_loss"] == 9.0
    assert conclusion["plan"]["conditions"][0]["kind"] == "full"
    assert conclusion["symbol"] == "600519.SH"


def test_extract_conclusion_carries_validated_portfolio_decision_directly():
    skill = StockAnalysisSkill()
    decision = PortfolioDecision(
        rating=PortfolioRating.UNDERWEIGHT,
        executive_summary="反弹减仓，控制敞口。",
        investment_thesis="回购催化已被价格透支，新增质押风险改变风险收益比。",
        price_target=12.0,
        confidence=81,
        trade_plan=TradePlan(
            plan_action=PlanAction.REDUCE,
            action_zone=[14.8, 15.2],
            invalidation_level=15.5,
            objective_levels=[13.5, 12.0],
            conditions=[
                TradeCondition(
                    kind="entry",
                    trigger_action=PlanAction.REDUCE,
                    description="反弹至压力区",
                )
            ],
        ),
        selection_reconciliation=SelectionReconciliation(
            prior_decision="BUY",
            alignment="reversal",
            decision_changed=True,
            explanation="新增质押风险改变结论",
            new_evidence=["质押比例上升"],
        ),
    )

    conclusion = skill._extract_conclusion(
        {"final_trade_decision": render_pm_decision(decision)},
        "600519.SH",
        structured_decision=decision.model_dump(mode="json"),
    )

    assert conclusion["rating"] == "Underweight"
    assert conclusion["target_price"] == 12.0
    assert conclusion["confidence"] == 81
    assert conclusion["reasons"] == [
        "反弹减仓，控制敞口。",
        "回购催化已被价格透支，新增质押风险改变风险收益比。",
    ]
    assert conclusion["plan"]["plan_action"] == "REDUCE"
    assert conclusion["plan"]["conditions"][0]["trigger_action"] == "REDUCE"
    assert conclusion["selection_reconciliation"]["new_evidence"] == ["质押比例上升"]


def test_100_share_holding_blocks_prose_only_partial_reduce_plan():
    conclusion = {
        "rating": "Underweight",
        "plan": {
            "plan_action": "REDUCE",
            "conditions": [
                {
                    "kind": "entry",
                    "trigger_action": "REDUCE",
                    "description": "首日无条件减持现有100股中的50股",
                },
                {
                    "kind": "full",
                    "trigger_action": "HOLD",
                    "description": "保留10股底仓等待反弹",
                },
                {
                    "kind": "stop",
                    "trigger_action": "EXIT",
                    "description": "跌破54.40元清仓剩余仓位",
                },
            ],
        },
    }

    result = _apply_holding_execution_constraints(
        conclusion,
        {"symbol": "601333.SH", "quantity": 100, "position_weight": 0.25},
        market="cn_a",
    )

    plan = result["plan"]
    assert plan["plan_action"] == "HOLD"
    assert plan["order_quantity"] is None
    assert plan["target_quantity"] == 100
    assert plan["position_pct"] == 25.0
    assert plan["conditions"][0]["trigger_action"] == "HOLD"
    assert "50股" not in plan["conditions"][0]["description"]
    assert plan["conditions"][1]["trigger_action"] == "HOLD"
    assert "10股" not in plan["conditions"][1]["description"]
    assert plan["conditions"][2]["trigger_action"] == "EXIT"
    assert plan["conditions"][2]["order_quantity"] == 100
    assert result["execution_validation"]["status"] == "adjusted"
    assert result["execution_validation"]["partial_sell_available"] is False


def test_structured_100_share_exit_is_normalized_to_full_quantity():
    conclusion = {
        "rating": "Sell",
        "plan": {"plan_action": "EXIT", "conditions": []},
    }

    result = _apply_holding_execution_constraints(
        conclusion,
        {"symbol": "601333.SH", "quantity": 100},
        market="cn_a",
    )

    assert result["plan"]["plan_action"] == "EXIT"
    assert result["plan"]["order_quantity"] == 100
    assert result["plan"]["target_quantity"] == 0
    assert result["execution_validation"]["status"] == "valid"


def test_selection_buy_to_underweight_requires_review_and_suspends_action():
    conclusion = {
        "rating": "Underweight",
        "plan": {"plan_action": "REDUCE"},
        "selection_reconciliation": {
            "alignment": "reversal",
            "explanation": "发现此前未覆盖的质押风险",
            "new_evidence": ["质押比例上升"],
        },
    }
    alignment = reconcile_selection_analysis(
        SELECTION,
        conclusion,
        analysis_date="2026-07-04",
    )
    candidate = {
        "final_decision": "BUY",
        "signal": "BUY",
        "position_pct": 5.0,
        "gate_reasons": ["quant_buy+llm_positive+catalyst"],
        "action_plan": {"position_pct": 5.0, "entry_zone": [10.0, 10.5]},
    }

    _apply_deep_analysis_safety_gate(candidate, alignment)

    assert alignment["status"] == "reversal"
    assert alignment["requires_review"] is True
    assert alignment["explicit_explanation"] is True
    assert candidate["pre_deep_final_decision"] == "BUY"
    assert candidate["final_decision"] == "HOLD_REVIEW"
    assert candidate["position_pct"] == 0.0
    assert candidate["pre_deep_action_plan"]["position_pct"] == 5.0
    assert candidate["action_plan"]["position_pct"] == 0.0


def test_deep_analysis_never_auto_promotes_watchlist():
    alignment = reconcile_selection_analysis(
        {**SELECTION, "final_decision": "WATCHLIST"},
        {"rating": "Buy", "plan": {"plan_action": "ENTER"}},
    )
    candidate = {"final_decision": "WATCHLIST", "position_pct": 0.0}

    _apply_deep_analysis_safety_gate(candidate, alignment)

    assert alignment["status"] == "upgrade"
    assert alignment["requires_review"] is True
    assert candidate["final_decision"] == "WATCHLIST"
    assert candidate["deep_analysis_review_required"] is True
