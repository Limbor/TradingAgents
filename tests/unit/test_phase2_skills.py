"""Tests for Phase 2 extension skills."""

import asyncio
import importlib

from tradingagents.core.persistence import Database
from tradingagents.core.signal_fusion import fuse_candidate_signal
from tradingagents.skills.daily_pipeline.skill import DailyPipelineInput, DailyPipelineSkill
from tradingagents.skills.market_scanner.skill import MarketScannerInput, MarketScannerSkill
from tradingagents.skills.portfolio_management.skill import PortfolioInput, PortfolioManagementSkill
from tradingagents.skills.risk_monitor.skill import RiskMonitorInput, RiskMonitorSkill
from tradingagents.skills.base import skill_progress

daily_pipeline_module = importlib.import_module("tradingagents.skills.daily_pipeline.skill")
market_scanner_module = importlib.import_module("tradingagents.skills.market_scanner.skill")


def _progress_stage_ids(events) -> set[str]:
    return {
        str(event.data.get("stage_id"))
        for event in events
        if event.event_type == "skill_progress"
    }


def test_skill_progress_event_contract():
    event = skill_progress(
        stage_id="research_bull",
        stage_label="多方观点",
        status="running",
        step_id="bull_researcher",
        step_label="多方观点进行中",
        detail="正在校验催化剂",
        agent="Bull Researcher",
        progress_pct=45,
        data={"round": 1},
    )

    assert event.event_type == "skill_progress"
    assert event.data == {
        "stage_id": "research_bull",
        "stage_label": "多方观点",
        "status": "running",
        "step_id": "bull_researcher",
        "step_label": "多方观点进行中",
        "detail": "正在校验催化剂",
        "agent": "Bull Researcher",
        "progress_pct": 45,
        "data": {"round": 1},
    }


def test_portfolio_skill_upsert_and_analyze(tmp_path):
    async def run():
        db = Database(tmp_path / "portfolio.db")
        skill = PortfolioManagementSkill()
        events = [
            event
            async for event in skill.execute(
                PortfolioInput(
                    action="upsert",
                    symbol="600519.SH",
                    quantity=10,
                    avg_cost=1500,
                    current_price=1650,
                ),
                {"db": db},
            )
        ]

        assert db.get_holding("600519.SH") is not None
        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        assert complete.data["summary"]["holding_count"] == 1
        assert complete.data["summary"]["unrealized_pnl"] == 1500
        assert {"prepare", "portfolio_mutation", "portfolio_metrics"} <= _progress_stage_ids(events)

    asyncio.run(run())


def test_market_scanner_returns_ranked_candidates():
    async def run():
        skill = MarketScannerSkill()
        events = [
            event
            async for event in skill.execute(
                MarketScannerInput(market="cn_a", min_score=60, limit=3),
                {"stockmanager_mcp_enabled": False, "market_scanner_demo_fallback": True},
            )
        ]

        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        candidates = complete.data["candidates"]
        assert complete.data["mcp_used"] is False
        assert 1 <= len(candidates) <= 3
        assert candidates == sorted(candidates, key=lambda item: item["score"], reverse=True)
        assert {"prepare", "quant_rank", "filter_sort", "report"} <= _progress_stage_ids(events)

    asyncio.run(run())


def test_daily_pipeline_runs_with_mcp_disabled(tmp_path):
    async def run():
        db = Database(tmp_path / "daily.db")
        skill = DailyPipelineSkill()
        events = [
            event
            async for event in skill.execute(
                DailyPipelineInput(limit=3, candidate_limit=5),
                {"db": db, "stockmanager_mcp_enabled": False, "daily_pipeline_demo_fallback": True},
            )
        ]

        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        assert complete.data["mcp_used"] is False
        assert len(complete.data["candidates"]) == 3
        reports = db.list_reports(ticker="DAILY_PIPELINE")
        assert len(reports) == 1
        assert len(db.list_signals(trade_date=complete.data["trade_date"])) == 3
        assert "Daily A-share Research Queue" in reports[0]["content"]
        assert {"prepare", "universe", "quant_rank", "llm_review", "report"} <= _progress_stage_ids(events)

    asyncio.run(run())


def test_daily_pipeline_uses_mcp_quant_rank(monkeypatch, tmp_path):
    class FakeMCPClient:
        async def rank_factor_candidates(self, **kwargs):
            return {
                "status": "success",
                "method": "cross_sectional_factor_rank_v1",
                "as_of_date": kwargs["trade_date"],
                "factor_profile": kwargs["factor_profile"],
                "universe": {"index": kwargs["universe_index"], "raw_size": 2, "tradable_size": 2, "ranked_size": 2},
                "strategy_meta": {"profile": "medium_term_balanced", "version": "v2"},
                "selection_meta": {"max_per_industry": kwargs.get("max_per_industry")},
                "concentration_meta": {"industry_counts": {"消费 白酒": 1, "新能源 电池": 1}},
                "rows": [
                    {
                        "rank": 1,
                        "ts_code": "600519.SH",
                        "name": "贵州茅台",
                        "industry": "消费 白酒",
                        "quant_score": 82.4,
                        "decision": "BUY",
                        "decision_reason": "quality and liquidity gates passed",
                        "gate_reasons": ["quality_gate_passed"],
                        "universe_percentile": 0.96,
                        "factor_scores": {"momentum": 72, "liquidity": 91, "quality": 96, "risk_control": 82},
                        "key_metrics": {"roe": 18.5, "amount_20d": 12.3},
                        "data_coverage": {"valuation": "available", "flow": "available"},
                        "factor_snapshot": {"latest_price": 1500.0},
                        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
                        "risk_flags": [],
                        "score_explain": ["quality top 5%", "no hard risk flags"],
                    },
                    {
                        "rank": 2,
                        "ts_code": "300750.SZ",
                        "name": "宁德时代",
                        "industry": "新能源 电池",
                        "quant_score": 74.0,
                        "universe_percentile": 0.90,
                        "factor_scores": {"momentum": 78, "liquidity": 88, "quality": 80, "risk_control": 70},
                        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
                        "risk_flags": [],
                        "score_explain": ["momentum above average"],
                    },
                ],
                "warnings": [],
            }

    async def fake_get_mcp_client(config):
        return FakeMCPClient()

    monkeypatch.setattr(daily_pipeline_module, "get_mcp_client", fake_get_mcp_client)

    async def run():
        db = Database(tmp_path / "daily-mcp.db")
        skill = DailyPipelineSkill()
        events = [
            event
            async for event in skill.execute(
                DailyPipelineInput(trade_date="2026-06-30", limit=2, candidate_limit=5),
                {"db": db, "run_id": "run-quant"},
            )
        ]

        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        candidates = complete.data["candidates"]
        assert complete.data["mcp_used"] is True
        assert candidates[0]["symbol"] == "600519.SH"
        assert candidates[0]["factor_data_source"] == "stockmanager_mcp"
        assert candidates[0]["fusion_mode"] == "quant_only"
        assert candidates[0]["quant_decision"] == "BUY"
        assert candidates[0]["final_decision"] == "WATCHLIST"
        assert candidates[0]["decision_stage"] == "llm_unavailable"
        assert candidates[0]["key_metrics"]["roe"] == 18.5
        assert complete.data["strategy_meta"]["version"] == "v2"
        assert complete.data["decision_pack"][0]["final_decision"] == "WATCHLIST"
        assert "Quant Evidence from StockManager" in candidates[0]["quant_evidence"]
        signals = db.list_signals(trade_date="2026-06-30")
        assert len(signals) == 2
        assert signals[0]["payload"]["symbol"] == "600519.SH"
        assert signals[0]["payload"]["strategy_meta"]["version"] == "v2"

    asyncio.run(run())


def test_daily_pipeline_can_exclude_dual_growth_boards(monkeypatch, tmp_path):
    calls = []

    class FakeMCPClient:
        async def rank_factor_candidates(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "success",
                "method": "cross_sectional_factor_rank_v1",
                "as_of_date": kwargs["trade_date"],
                "rows": [
                    {
                        "rank": 1,
                        "ts_code": "300308.SZ",
                        "name": "中际旭创",
                        "industry": "通信",
                        "quant_score": 90,
                        "factor_scores": {"momentum": 90, "liquidity": 95, "quality": 92, "risk_control": 55},
                        "tradability": {"is_tradable": True},
                    },
                    {
                        "rank": 2,
                        "ts_code": "688256.SH",
                        "name": "寒武纪",
                        "industry": "电子",
                        "quant_score": 88,
                        "factor_scores": {"momentum": 88, "liquidity": 90, "quality": 82, "risk_control": 50},
                        "tradability": {"is_tradable": True},
                    },
                    {
                        "rank": 3,
                        "ts_code": "603986.SH",
                        "name": "兆易创新",
                        "industry": "电子",
                        "quant_score": 78,
                        "factor_scores": {"momentum": 78, "liquidity": 85, "quality": 80, "risk_control": 60},
                        "tradability": {"is_tradable": True},
                    },
                    {
                        "rank": 4,
                        "ts_code": "600519.SH",
                        "name": "贵州茅台",
                        "industry": "消费 白酒",
                        "quant_score": 76,
                        "factor_scores": {"momentum": 60, "liquidity": 90, "quality": 96, "risk_control": 80},
                        "tradability": {"is_tradable": True},
                    },
                ],
            }

    async def fake_get_mcp_client(config):
        return FakeMCPClient()

    monkeypatch.setattr(daily_pipeline_module, "get_mcp_client", fake_get_mcp_client)

    async def run():
        db = Database(tmp_path / "daily-board.db")
        skill = DailyPipelineSkill()
        events = [
            event
            async for event in skill.execute(
                DailyPipelineInput(
                    trade_date="2026-07-01",
                    limit=2,
                    candidate_limit=20,
                    board_filter="main_board",
                ),
                {"db": db, "daily_pipeline_llm_review_enabled": False},
            )
        ]
        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        candidates = complete.data["candidates"]
        assert calls[0]["limit"] > 2
        assert calls[0]["enable_decision"] is True
        assert calls[0]["max_per_industry"] == 3
        assert calls[0]["filters"]["board_filter"] == "main_board"
        assert [item["symbol"] for item in candidates] == ["603986.SH", "600519.SH"]
        assert all(item["board"] == "main" for item in candidates)
        candidates_event = [event for event in events if event.event_type == "daily_pipeline_candidates"][-1]
        assert candidates_event.data["board_filter"] == "main_board"
        assert any("removed chinext:1" in item and "star:1" in item for item in candidates_event.data["warnings"])

    asyncio.run(run())


def test_daily_pipeline_maps_dual_growth_filter_for_mcp(monkeypatch, tmp_path):
    calls = []

    class FakeMCPClient:
        async def rank_factor_candidates(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "success",
                "method": "cross_sectional_factor_rank_v1",
                "as_of_date": kwargs["trade_date"],
                "rows": [
                    {
                        "rank": 1,
                        "ts_code": "300308.SZ",
                        "name": "中际旭创",
                        "industry": "通信",
                        "quant_score": 72,
                        "factor_scores": {},
                        "tradability": {"is_tradable": True},
                    },
                ],
            }

    async def fake_get_mcp_client(config):
        return FakeMCPClient()

    monkeypatch.setattr(daily_pipeline_module, "get_mcp_client", fake_get_mcp_client)

    async def run():
        db = Database(tmp_path / "daily-dual-growth.db")
        skill = DailyPipelineSkill()
        events = [
            event
            async for event in skill.execute(
                DailyPipelineInput(
                    trade_date="2026-07-01",
                    limit=1,
                    candidate_limit=20,
                    board_filter="dual_growth_only",
                ),
                {"db": db, "daily_pipeline_llm_review_enabled": False},
            )
        ]
        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        assert complete.data["candidates"][0]["symbol"] == "300308.SZ"

    asyncio.run(run())
    assert calls[0]["filters"]["board_filter"] == "chinext_star"


def test_daily_pipeline_fuses_llm_review(monkeypatch, tmp_path):
    class FakeMCPClient:
        async def rank_factor_candidates(self, **kwargs):
            return {
                "status": "success",
                "method": "cross_sectional_factor_rank_v1",
                "as_of_date": kwargs["trade_date"],
                "rows": [
                    {
                        "rank": 1,
                        "ts_code": "600519.SH",
                        "name": "贵州茅台",
                        "industry": "消费 白酒",
                        "quant_score": 82.0,
                        "factor_scores": {"momentum": 70, "liquidity": 92, "quality": 96, "risk_control": 88},
                        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
                        "risk_flags": [],
                        "score_explain": ["quality top bucket"],
                    }
                ],
            }

    class FakeReviewer:
        async def review(self, candidate):
            return {
                "llm_view": "negative",
                "llm_confidence": 42,
                "risk_override": False,
                "invalidates_quant": True,
                "key_catalysts": [],
                "key_risks": ["催化剂不足"],
                "risk_flags": ["llm_no_catalyst"],
                "reasoning": "量化质量分强，但缺少明确新增催化剂。",
            }

    async def fake_get_mcp_client(config):
        return FakeMCPClient()

    monkeypatch.setattr(daily_pipeline_module, "get_mcp_client", fake_get_mcp_client)

    async def run():
        db = Database(tmp_path / "daily-llm.db")
        skill = DailyPipelineSkill()
        events = [
            event
            async for event in skill.execute(
                DailyPipelineInput(trade_date="2026-06-30", limit=1, candidate_limit=5),
                {
                    "db": db,
                    "run_id": "run-fused",
                    "daily_pipeline_llm_reviewer": FakeReviewer(),
                    "daily_pipeline_llm_review_limit": 1,
                },
            )
        ]

        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        candidate = complete.data["candidates"][0]
        assert complete.data["review_meta"]["reviewed"] == 1
        assert candidate["fusion_mode"] == "quant_llm_fused"
        assert candidate["llm_confidence"] == 42
        assert candidate["final_decision"] == "SKIP"
        assert "llm_invalidates_quant" in candidate["gate_reasons"]
        assert db.list_signals(trade_date="2026-06-30")[0]["fusion_mode"] == "quant_llm_fused"

    asyncio.run(run())


def test_daily_pipeline_falls_back_when_universe_empty(monkeypatch, tmp_path):
    class FakeMCPClient:
        async def rank_factor_candidates(self, **kwargs):
            if kwargs["trade_date"] == "2026-07-01":
                return {
                    "status": "error",
                    "message": "UNIVERSE_EMPTY: 指数成分股为空",
                    "method": "cross_sectional_factor_rank_v1",
                    "as_of_date": "2026-07-01",
                }
            return {
                "status": "success",
                "method": "cross_sectional_factor_rank_v1",
                "as_of_date": kwargs["trade_date"],
                "rows": [
                    {
                        "rank": 1,
                        "ts_code": "300308.SZ",
                        "name": "中际旭创",
                        "industry": "通信设备",
                        "quant_score": 71.4,
                        "factor_scores": {"momentum": 80, "liquidity": 90, "quality": 72, "risk_control": 65},
                        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
                        "risk_flags": [],
                    }
                ],
            }

    async def fake_get_mcp_client(config):
        return FakeMCPClient()

    monkeypatch.setattr(daily_pipeline_module, "get_mcp_client", fake_get_mcp_client)

    async def run():
        db = Database(tmp_path / "daily-fallback.db")
        skill = DailyPipelineSkill()
        events = [
            event
            async for event in skill.execute(
                DailyPipelineInput(trade_date="2026-07-01", limit=1, candidate_limit=5),
                {"db": db, "daily_pipeline_llm_review_enabled": False},
            )
        ]
        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        assert complete.data["candidates"][0]["symbol"] == "300308.SZ"
        assert complete.data["quant_meta"]["as_of_date"] == "2026-06-30"
        candidates_event = [event for event in events if event.event_type == "daily_pipeline_candidates"][-1]
        assert any("used previous available ranking date 2026-06-30" in item for item in candidates_event.data["warnings"])

    asyncio.run(run())


def test_market_scanner_uses_mcp_quant_rank(monkeypatch):
    class FakeMCPClient:
        async def rank_factor_candidates(self, **kwargs):
            return {
                "status": "success",
                "method": "cross_sectional_factor_rank_v1",
                "as_of_date": kwargs["trade_date"],
                "rows": [
                    {
                        "rank": 1,
                        "ts_code": "600519.SH",
                        "name": "贵州茅台",
                        "industry": "消费 白酒",
                        "quant_score": 82,
                        "factor_scores": {"momentum": 72, "liquidity": 91, "quality": 96, "risk_control": 82},
                        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
                        "risk_flags": [],
                        "score_explain": ["quality top 5%"],
                    }
                ],
            }

    async def fake_get_mcp_client(config):
        return FakeMCPClient()

    monkeypatch.setattr(market_scanner_module, "get_mcp_client", fake_get_mcp_client)

    async def run():
        skill = MarketScannerSkill()
        events = [
            event
            async for event in skill.execute(
                MarketScannerInput(market="cn_a", min_score=60, limit=3),
                {"market_scanner_trade_date": "2026-06-30"},
            )
        ]
        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        assert complete.data["mcp_used"] is True
        assert complete.data["candidates"][0]["symbol"] == "600519.SH"
        assert complete.data["candidates"][0]["signal"] == "WATCHLIST"

    asyncio.run(run())


def test_signal_fusion_risk_gate_blocks_buy():
    candidate = {
        "symbol": "600519.SH",
        "quant_score": 88,
        "tradability": {"is_tradable": True},
        "risk_flags": [],
    }
    signal = fuse_candidate_signal(
        candidate,
        "medium_term",
        {"llm_confidence": 90, "risk_override": True, "risk_flags": ["重大诉讼"]},
    )
    assert signal["signal"] == "HOLD_REVIEW"
    assert "llm_risk_override" in signal["gate_reasons"]


def test_risk_monitor_runs_with_empty_portfolio(tmp_path):
    async def run():
        db = Database(tmp_path / "risk.db")
        skill = RiskMonitorSkill()
        events = [
            event
            async for event in skill.execute(
                RiskMonitorInput(),
                {"db": db, "stockmanager_mcp_enabled": False},
            )
        ]

        complete = [event for event in events if event.event_type == "skill_complete"][-1]
        assert complete.data["mcp_used"] is False
        assert complete.data["holdings"] == []
        assert complete.data["risks"] == []
        assert {"prepare", "announcement_scan", "risk_summary"} <= _progress_stage_ids(events)

    asyncio.run(run())
