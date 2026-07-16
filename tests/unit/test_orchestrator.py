"""Tests for Phase 2 natural language routing."""

import asyncio

from tradingagents.core.orchestrator import Orchestrator
from tradingagents.skills.daily_pipeline.skill import DailyPipelineSkill
from tradingagents.skills.daily_review.skill import DailyReviewSkill
from tradingagents.skills.decision_audit.skill import DecisionAuditSkill
from tradingagents.skills.market_scanner.skill import MarketScannerSkill
from tradingagents.skills.portfolio_management.skill import PortfolioManagementSkill
from tradingagents.skills.registry import SkillRegistry
from tradingagents.skills.risk_monitor.skill import RiskMonitorSkill
from tradingagents.skills.stock_analysis.skill import StockAnalysisSkill
from tradingagents.skills.strategy_backtest.skill import StrategyBacktestSkill


def _registry():
    registry = SkillRegistry()
    registry.register(StockAnalysisSkill())
    registry.register(PortfolioManagementSkill())
    registry.register(MarketScannerSkill())
    registry.register(DailyPipelineSkill())
    registry.register(DailyReviewSkill())
    registry.register(RiskMonitorSkill())
    registry.register(DecisionAuditSkill())
    registry.register(StrategyBacktestSkill())
    return registry


def test_route_decision_audit_and_backtest():
    async def run():
        audit = await Orchestrator(_registry()).route("评估到期决策")
        assert audit.skill is not None
        assert audit.skill.metadata.id == "decision_audit"

        backtest = await Orchestrator(_registry()).route(
            "策略回测 2024-01-01 到 2025-01-01"
        )
        assert backtest.skill is not None
        assert backtest.skill.metadata.id == "strategy_backtest"
        assert backtest.params == {"start_date": "2024-01-01", "end_date": "2025-01-01"}

    asyncio.run(run())


def test_route_chinese_stock_name_to_analysis():
    async def run():
        route = await Orchestrator(_registry()).route("帮我看看茅台")
        assert route.skill is not None
        assert route.skill.metadata.id == "stock_analysis"
        assert route.params["ticker"] == "600519.SH"

    asyncio.run(run())


def test_route_portfolio_upsert_extracts_basic_numbers():
    async def run():
        route = await Orchestrator(_registry()).route("添加持仓 601899.SH 200 18.5 20.1")
        assert route.skill is not None
        assert route.skill.metadata.id == "portfolio_management"
        assert route.params["action"] == "upsert"
        assert route.params["symbol"] == "601899.SH"
        assert route.params["quantity"] == 200
        assert route.params["avg_cost"] == 18.5
        assert route.params["current_price"] == 20.1

    asyncio.run(run())


def test_route_holding_stock_analysis_prioritizes_analysis_intent():
    async def run():
        route = await Orchestrator(_registry()).route("帮我分析我的持仓茅台")
        assert route.skill is not None
        assert route.skill.metadata.id == "stock_analysis"
        assert route.params["ticker"] == "600519.SH"

    asyncio.run(run())


def test_route_market_scanner_extracts_market_and_limit():
    async def run():
        route = await Orchestrator(_registry()).route("筛选美股 AI top 3 score 60")
        assert route.skill is not None
        assert route.skill.metadata.id == "market_scanner"
        assert route.params["market"] == "us"
        assert route.params["limit"] == 3
        assert route.params["min_score"] == 60
        assert route.params["theme"] == "AI"

    asyncio.run(run())


def test_route_daily_pipeline():
    async def run():
        route = await Orchestrator(_registry()).route("今日机会 每日选股 top 4")
        assert route.skill is not None
        assert route.skill.metadata.id == "daily_pipeline"
        assert route.params["limit"] == 4

    asyncio.run(run())


def test_route_daily_pipeline_main_board_filter():
    async def run():
        route = await Orchestrator(_registry()).route("每日选股 top 5 非双创 主板")
        assert route.skill is not None
        assert route.skill.metadata.id == "daily_pipeline"
        assert route.params["limit"] == 5
        assert route.params["board_filter"] == "main_board"

    asyncio.run(run())


def test_route_daily_review_main_board_filter():
    async def run():
        route = await Orchestrator(_registry()).route("收盘复盘 top 5 主板")
        assert route.skill is not None
        assert route.skill.metadata.id == "daily_review"
        assert route.params["daily_limit"] == 5
        assert route.params["board_filter"] == "main_board"

    asyncio.run(run())


def test_route_risk_monitor():
    async def run():
        route = await Orchestrator(_registry()).route("扫描最近45天持仓风险")
        assert route.skill is not None
        assert route.skill.metadata.id == "risk_monitor"
        assert route.params["lookback_days"] == 45

    asyncio.run(run())
