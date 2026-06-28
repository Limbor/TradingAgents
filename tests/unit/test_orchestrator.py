"""Tests for Phase 2 natural language routing."""

import asyncio

from tradingagents.core.orchestrator import Orchestrator
from tradingagents.skills.registry import SkillRegistry
from tradingagents.skills.stock_analysis.skill import StockAnalysisSkill
from tradingagents.skills.portfolio_management.skill import PortfolioManagementSkill
from tradingagents.skills.market_scanner.skill import MarketScannerSkill


def _registry():
    registry = SkillRegistry()
    registry.register(StockAnalysisSkill())
    registry.register(PortfolioManagementSkill())
    registry.register(MarketScannerSkill())
    return registry


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
