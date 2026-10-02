"""Tests for Phase 2 natural language routing."""

import asyncio

from tradingagents.core.orchestrator import (
    Orchestrator,
    _extract_ticker,
    merge_context_params,
)
from tradingagents.skills.daily_pipeline.skill import DailyPipelineSkill
from tradingagents.skills.daily_review.skill import DailyReviewSkill
from tradingagents.skills.decision_audit.skill import DecisionAuditSkill
from tradingagents.skills.market_overview.skill import MarketOverviewSkill
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
    registry.register(MarketOverviewSkill())
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


def test_route_prefers_precise_company_name_over_short_alias():
    async def run():
        bank = await Orchestrator(_registry()).route("分析平安银行")
        insurer = await Orchestrator(_registry()).route("分析中国平安")
        assert bank.params["ticker"] == "000001.SZ"
        assert insurer.params["ticker"] == "601318.SH"

    asyncio.run(run())


def test_extract_ticker_does_not_promote_ordinary_english_words():
    assert _extract_ticker("what is risk?") is None
    assert _extract_ticker("hello there") is None
    assert _extract_ticker("should I hold?") is None
    assert _extract_ticker("analyze AAPL") == "AAPL"
    assert _extract_ticker("$tsla") == "TSLA"


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


def test_route_portfolio_upsert_rejects_zero_avg_cost():
    """Placeholder zeros (e.g. old '添加持仓 X 100 0 0' button) must not be
    persisted or replaced by an invented default cost basis."""
    async def run():
        route = await Orchestrator(_registry()).route("添加持仓 601899.SH 100 0 0")
        assert route.skill is None
        assert "avg cost" in route.reason

    asyncio.run(run())


def test_route_daily_pipeline_industry_restriction():
    async def run():
        route = await Orchestrator(_registry()).route("每日选股 top 5，只看半导体行业")
        assert route.skill is not None
        assert route.skill.metadata.id == "daily_pipeline"
        assert route.params["limit"] == 5
        assert route.params["industries"] == ["半导体"]

    asyncio.run(run())


def test_route_qualitative_sector_analysis_to_market_overview():
    """Drivers/sustainability need sector evidence, not a candidate ranking."""
    async def run():
        route = await Orchestrator(_registry()).route("分析今天钨板块的驱动逻辑和持续性")
        assert route.skill is not None
        assert route.skill.metadata.id == "market_overview"
        assert route.params["focus_industries"] == ["钨"]

    asyncio.run(run())


def test_route_sector_stock_selection_stays_on_daily_pipeline():
    async def run():
        route = await Orchestrator(_registry()).route("分析今天煤炭板块的股票")
        assert route.skill is not None
        assert route.skill.metadata.id == "daily_pipeline"
        assert route.params["industries"] == ["煤炭"]

    asyncio.run(run())


def test_merge_context_params_schema_driven():
    registry = _registry()
    stock = registry.get("stock_analysis")
    pipeline = registry.get("daily_pipeline")

    # Declared schema fields are merged; undeclared keys are not injected.
    params = merge_context_params(
        stock,
        {"ticker": "600519.SH"},
        {"holding_context": {"symbol": "600519.SH"}, "news_context": {"title": "x"}},
    )
    assert params["holding_context"] == {"symbol": "600519.SH"}
    assert "news_context" not in params

    # Explicit params always win over context (setdefault semantics).
    params = merge_context_params(
        stock,
        {"holding_context": {"symbol": "A"}},
        {"holding_context": {"symbol": "B"}},
    )
    assert params["holding_context"]["symbol"] == "A"

    # Skills without declared context fields receive nothing.
    params = merge_context_params(pipeline, {}, {"holding_context": {"symbol": "600519.SH"}})
    assert params == {}


def test_route_hint_unknown_skill():
    route = Orchestrator(_registry()).route_hint({"skill_id": "nope", "params": {}})
    assert route.skill is None
    assert "unknown skill" in route.reason


def test_route_hint_validation_failure():
    route = Orchestrator(_registry()).route_hint(
        {"skill_id": "daily_pipeline", "params": {"limit": "not-a-number"}}
    )
    assert route.skill is None
    assert "validation failed" in route.reason


def test_route_hint_stock_analysis_enriched_and_context_merged():
    route = Orchestrator(_registry()).route_hint(
        {"skill_id": "stock_analysis", "params": {"ticker": "600519.SH"}},
        context={"holding_context": {"symbol": "600519.SH", "quantity": 100}},
    )
    assert route.skill is not None
    assert route.skill.metadata.id == "stock_analysis"
    assert route.confidence == 1.0
    assert route.reason == "intent_hint"
    assert route.params["holding_context"]["quantity"] == 100
    # Hint-routed analyses get the same defaults as regex-routed ones.
    assert route.params["ticker_name"]
    assert route.params["analysis_date"]


def test_route_hint_stock_analysis_missing_ticker_degrades():
    route = Orchestrator(_registry()).route_hint({"skill_id": "stock_analysis", "params": {}})
    assert route.skill is None
    assert "validation failed" in route.reason


def test_route_hint_daily_pipeline_params_pass_through():
    route = Orchestrator(_registry()).route_hint(
        {"skill_id": "daily_pipeline", "params": {"limit": 5, "industries": ["半导体"]}}
    )
    assert route.skill is not None
    assert route.skill.metadata.id == "daily_pipeline"
    assert route.params == {"limit": 5, "industries": ["半导体"]}
