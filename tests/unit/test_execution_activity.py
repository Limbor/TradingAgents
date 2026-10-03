"""Progress describes real operations, with stable identities and safe text."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tradingagents.core.activity_labels import activity_detail, report_activity, tool_action
from tradingagents.core.candidate_review_runner import apply_llm_reviews
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.skills._shared import drive_with_progress


def test_activity_detail_excludes_credentials_and_nested_payloads():
    detail = activity_detail({"ts_code": "600519.SH", "date": "2026-09-30",
                              "api_key": "secret", "context": {"private": "text"}})
    assert detail == "标的 600519.SH · 基准日 2026-09-30"
    assert activity_detail({"api_key": "secret"}) is None
    assert tool_action("get_mcp_risk_announcements") == "核对风险公告"
    assert tool_action("unknown_tool") == "查询分析所需数据"


@pytest.mark.asyncio
async def test_stream_emits_real_tool_lifecycle_and_repeated_agent_rounds():
    gate = asyncio.Event()

    async def stream(*_args, **_kwargs):
        yield {"event": "on_chain_start", "name": "Market Analyst", "run_id": "agent-1", "data": {}}
        for call_id in ("price-1", "price-2"):
            yield {"event": "on_tool_start", "name": "get_stock_data", "run_id": call_id,
                   "data": {"input": {"symbol": "600519.SH"}}}
        await gate.wait()
        yield {"event": "on_tool_end", "name": "get_stock_data", "run_id": "price-2",
               "data": {"output": SimpleNamespace(status="error")}}
        yield {"event": "on_tool_end", "name": "get_stock_data", "run_id": "price-1", "data": {"output": "ok"}}
        yield {"event": "on_chain_end", "name": "Market Analyst", "run_id": "agent-1", "data": {"output": {}}}
        yield {"event": "on_chain_start", "name": "Market Analyst", "run_id": "agent-2", "data": {}}
        yield {"event": "on_chain_end", "name": "Market Analyst", "run_id": "agent-2", "data": {"output": {}}}

    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    graph._resolve_pending_entries = Mock()
    graph.resolve_instrument_context = Mock(return_value={})
    graph._merge_memory_context = Mock(return_value="")
    graph.memory_log = Mock()
    graph.propagator = Mock()
    graph.propagator.create_initial_state.return_value = {}
    graph.propagator.get_graph_args.return_value = {}
    graph.graph_setup = Mock()
    graph.graph_setup.setup_graph.return_value.compile.return_value = SimpleNamespace(astream_events=stream)
    events = graph.astream_propagate("600519.SH", "2026-09-30")
    start = [await anext(events) for _ in range(3)]
    assert [e["data"]["status"] for e in start] == ["running"] * 3
    pending = asyncio.create_task(anext(events))
    await asyncio.sleep(0)
    assert not pending.done(), "completion must wait for the actual tool"
    gate.set()
    rest = [await pending, *[e async for e in events]]
    tool_events = [e["data"] for e in [*start, *rest] if e["type"] == "tool_call"]
    assert [(e["activity_id"], e["status"]) for e in tool_events] == [
        ("price-1", "running"), ("price-2", "running"), ("price-2", "failed"), ("price-1", "completed")]
    agents = [e["data"] for e in [*start, *rest] if e["type"] == "agent_status"]
    assert [e["activity_id"] for e in agents] == ["agent-1", "agent-1", "agent-2", "agent-2"]


@pytest.mark.asyncio
async def test_parallel_candidate_review_progress_and_failure_are_isolated():
    gate = asyncio.Event()

    class Reviewer:
        async def review(self, candidate):
            await gate.wait()
            if candidate["symbol"] == "600001.SH":
                raise RuntimeError("test failure")
            return {"view": "neutral", "confidence": .5, "score": 50}

    candidates = [{"symbol": code, "quant_score": 70} for code in ("600000.SH", "600001.SH")]
    events = drive_with_progress(lambda callback: apply_llm_reviews(
        candidates, config={"_activity_progress": callback}, reviewer=Reviewer(),
        trade_date="2026-09-30", style="medium_term", enrich=False,
    ))
    started = [await anext(events) for _ in range(2)]
    assert all(kind == "progress" and value["status"] == "running" for kind, value in started)
    gate.set()
    rest = [e async for e in events]
    progress = [value for kind, value in rest if kind == "progress"]
    assert {e["activity_id"]: e["status"] for e in progress} == {
        "review:600000.SH": "completed", "review:600001.SH": "failed"}
    assert rest[-1][0] == "result"


@pytest.mark.asyncio
async def test_progress_observer_cannot_break_analysis():
    async def broken(_update):
        raise RuntimeError("observer disconnected")
    await report_activity({"_activity_progress": broken}, "read", "查询股票价格", "running")


def test_market_fetch_reports_each_source_and_marks_missing_data(monkeypatch):
    import pandas as pd

    from tradingagents.dataflows import (
        akshare_cn_specific as cn,
        akshare_macro,
        akshare_news,
        tushare_common,
    )
    from tradingagents.skills.market_overview.skill import MarketOverviewInput, _fetch_market_data

    def missing(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(cn, "get_market_indices_overview", missing)
    monkeypatch.setattr(cn, "get_market_breadth", lambda _: {"up": 10, "down": 5})
    monkeypatch.setattr(cn, "get_northbound_summary", lambda _: {"latest_net": None})
    monkeypatch.setattr(cn, "get_board_heat", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(tushare_common, "get_standard_industry_heat", lambda *args, **kwargs: [])
    monkeypatch.setattr(akshare_news, "get_market_news_flash", lambda *args, **kwargs: [])
    monkeypatch.setattr(akshare_macro, "get_macro_snapshot", lambda _: [])
    monkeypatch.setattr(cn, "get_market_unlock_overview", lambda *args, **kwargs: [])
    progress = []
    _fetch_market_data("2026-09-30", MarketOverviewInput(), [], progress.append)
    assert len(progress) == 16
    assert [(e["activity_id"], e["status"]) for e in progress[:4]] == [
        ("fetch:indices", "running"), ("fetch:indices", "failed"),
        ("fetch:breadth", "running"), ("fetch:breadth", "completed")]
    assert all("2026-09-30" in e["detail"] for e in progress)
    assert "provider unavailable" not in str(progress)


@pytest.mark.asyncio
async def test_stock_skill_translates_tool_states_without_merging_agent_stage(monkeypatch):
    import importlib

    from tradingagents.graph import trading_graph
    module = importlib.import_module("tradingagents.skills.stock_analysis.skill")

    class FakeGraph:
        def __init__(self, **kwargs):
            pass

        async def astream_propagate(self, **kwargs):
            for status in ("running", "completed"):
                yield {"type": "tool_call", "data": {"tool": "get_stock_data", "activity_id": "price",
                       "status": status, "args": {"symbol": "600000.SH", "api_key": "secret"}}}

    temporal = SimpleNamespace(market_asof_date="2026-09-30", info_cutoff="2026-09-30", to_dict=lambda: {})
    monkeypatch.setattr(module, "resolve_temporal_context", lambda *args, **kwargs: (temporal, kwargs["params"]))
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FakeGraph)
    iterator = module.StockAnalysisSkill().execute(
        module.StockAnalysisInput(ticker="600000.SH", analysis_date="2026-09-30", include_portfolio_context=False), {})
    progress = []
    try:
        async for event in iterator:
            if event.event_type == "skill_progress" and event.data.get("activity_id") == "price":
                progress.append(event.data)
                if event.data["status"] == "completed":
                    break
    finally:
        await iterator.aclose()
    assert [e["status"] for e in progress] == ["running", "completed"]
    assert progress[0]["step_label"] == "正在查询股票价格"
    assert progress[1]["stage_id"] == "tool_price"
    assert progress[0]["detail"] == "标的 600000.SH"


@pytest.mark.asyncio
@pytest.mark.parametrize("holding_params", [
    {},
    {"include_portfolio_context": True},
    {"holding_context": {"symbol": "600487.SH", "quantity": 100, "current_price": 69.49}},
])
async def test_ordinary_stock_research_does_not_load_cached_holdings(monkeypatch, tmp_path, holding_params):
    import importlib

    from tradingagents.core.persistence import Database
    from tradingagents.graph import trading_graph
    module = importlib.import_module("tradingagents.skills.stock_analysis.skill")
    db = Database(tmp_path / "stock-context.db")
    db.upsert_holding("600487.SH", quantity=100, avg_cost=50, current_price=69.49)

    async def forbidden_cache(*args, **kwargs):
        raise AssertionError("ordinary research must not inject cached holdings")
    class FakeGraph:
        def __init__(self, *, config, **kwargs):
            assert config["holding_context"] is None
            assert "69.49" not in config["memory_extra_context"]
        async def astream_propagate(self, **kwargs):
            yield {"type": "agent_status", "data": {"agent": "Market Analyst", "status": "running"}}
    temporal = SimpleNamespace(market_asof_date="2026-09-30", info_cutoff="2026-09-30", to_dict=lambda: {})
    monkeypatch.setattr(module, "resolve_temporal_context", lambda *args, **kwargs: (temporal, kwargs["params"]))
    monkeypatch.setattr(module, "_load_holding_context", forbidden_cache)
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FakeGraph)
    iterator = module.StockAnalysisSkill().execute(
        module.StockAnalysisInput(ticker="600487.SH", analysis_date="2026-09-30", **holding_params), {"db": db})
    try:
        async for event in iterator:
            if event.event_type == "agent_status":
                break
    finally:
        await iterator.aclose()
