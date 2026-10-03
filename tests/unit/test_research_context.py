"""Local regression for incremental research and loss-aware report handoffs."""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from tradingagents.core.agent_harness import AgentStore
from tradingagents.core.agent_runtime import (
    AgentContext,
    AgentSpec,
    role_node,
    use_context,
)
from tradingagents.core.llm_usage import estimate_tokens, summarize_usage
from tradingagents.core.model_policy import resolve_model
from tradingagents.core.persistence import Database
from tradingagents.core.research_context import (
    REPORT_ROLES,
    compact_research_state,
    load_reusable_research,
    report_digest,
    research_policy_key,
    restore_debate_history,
    select_reusable_research,
)

NOW = datetime.now(timezone.utc)
POLICY = {"llm_provider": "qianwen", "model_policy": {"default_model": "qwen3.8-max"}}


def receipt(**changes):
    return {"run_id": "analyst", "root_id": "task", "kind": "agent", "role": "Market Analyst",
            "symbols": ["600487.SH"], "as_of_date": "2026-09-30", "info_cutoff": NOW.isoformat(),
            "completed_at": NOW.isoformat(), "status": "completed", "source_task_completed": True,
            "research_policy_key": research_policy_key(POLICY),
            "output": {"market_report": "收盘价 30.25 元，RSI 64.3。风险：跌破 28.80 元需重新评估。"},
            "tool_observations": [{"run_id": "price", "tool": "get_verified_market_snapshot", "status": "completed"}],
            "evidence_refs": ["price"], **changes}


def select(records, **changes):
    return select_reusable_research(records, symbol="600487.SH", as_of_date="2026-09-30",
        info_cutoff=NOW.isoformat(), policy_key=research_policy_key(POLICY), now=NOW, **changes)


def test_reuse_keeps_original_cutoff_and_expiry_across_repeated_followups():
    first = receipt()
    cached = select([first])["market_report"]
    assert cached["source"]["run_id"] == "analyst"
    repeated = receipt(run_id="repeated", completed_at=(NOW + timedelta(minutes=20)).isoformat(), reused_from=cached["source"])
    assert select([repeated])["market_report"]["source"] == cached["source"]
    assert not select_reusable_research([repeated], symbol="600487.SH", as_of_date="2026-09-30",
        info_cutoff=(NOW + timedelta(minutes=31)).isoformat(), policy_key=research_policy_key(POLICY),
        now=NOW + timedelta(minutes=31))


@pytest.mark.parametrize("changes", [
    {"status": "failed"}, {"source_task_completed": False}, {"as_of_date": "2026-09-29"},
    {"completed_at": (NOW - timedelta(minutes=31)).isoformat()}, {"completed_at": None},
    {"info_cutoff": (NOW + timedelta(minutes=1)).isoformat()},
    {"research_policy_key": "another-model"}, {"evidence_refs": []},
    {"tool_observations": []},
    {"output": {"market_report": "行情不可用，无法核验价格。"}},
    {"tool_observations": [{"tool": "get_theme_heat", "status": "failed"}]},
])
def test_newest_invalid_attempt_blocks_older_success(changes):
    assert not select([receipt(run_id="old"), receipt(run_id="new", **changes)])


def test_other_symbol_and_other_conversation_are_never_reused(tmp_path):
    assert not select([receipt(symbols=["600519.SH"])])
    db = Database(tmp_path / "reuse.db")
    store = AgentStore(db)
    first_c = store.create_conversation("股票研究", None)
    first = store.create_task(first_c["id"], "分析")
    db.save_agent_runtime(receipt(root_id=first["id"]))
    store.set_status(first["id"], "completed")
    other_c = store.create_conversation("另一对话", None)
    other = store.create_task(other_c["id"], "同一股票")
    scope = {"symbol": "600487.SH", "as_of_date": "2026-09-30", "info_cutoff": NOW.isoformat(),
             "policy_key": research_policy_key(POLICY), "now": NOW}
    assert not load_reusable_research(db, other_c["id"], other["id"], **scope)
    followup = store.create_task(first_c["id"], "补充")
    assert load_reusable_research(db, first_c["id"], followup["id"], **scope)["market_report"]
    # A future task's record cannot leak into an earlier request.
    assert not load_reusable_research(db, first_c["id"], first["id"], **scope)


def test_digest_keeps_exact_numbers_risks_gaps_sources_and_discloses_omissions():
    text = "\n".join(f"背景描述第 {i} 段，行业公司持续经营。" for i in range(300))
    text += "\n来源：财报 2026-06-30。\n营收 108.25 亿元，同比增长 12.34%。\n"
    text += "风险：现金流 -1.25 亿元；止损条件为跌破 28.80 元。\n行业估值分位缺失，不能判断是否低估。\n"
    digest = report_digest(text, budget=1000, as_of_date="2026-09-30", source={"run_id": "source"})
    serialized = json.dumps(digest, ensure_ascii=False)
    assert "108.25" in serialized and "12.34%" in serialized and "-1.25" in serialized and "28.80" in serialized
    assert digest["gaps"] and digest["risks"] and digest["sources"]
    assert digest["partial"] and digest["omitted_units"] > 0
    assert estimate_tokens(digest) < 1100
    assert estimate_tokens(digest) < estimate_tokens(text) / 3
    failure = report_digest("营收有所增长。", observations=[{"tool": "get_cashflow", "status": "failed", "error_type": "OSError"}])
    assert "get_cashflow" in failure["gaps"][0]


def test_compact_view_and_debate_restoration_preserve_original_full_reports():
    report = "\n".join(f"营收 {i}.25 亿元，同比增长 12.34%。" for i in range(500))
    history = "Bull Analyst: " + "\n".join(f"论据 {i}；风险：现金流不足。" for i in range(200))
    original = {"trade_date": "2026-09-30", "market_report": report,
                "investment_debate_state": {"history": history, "bull_history": history, "count": 1}}
    saved = copy.deepcopy(original)
    view, stats = compact_research_state(original)
    assert original == saved and stats["saved_tokens_estimate"] > 0
    assert estimate_tokens(view["market_report"]) < 1100
    output = {"investment_debate_state": {**view["investment_debate_state"],
              "history": view["investment_debate_state"]["history"] + "\nBear Analyst: 新风险。"}}
    restored = restore_debate_history(output, original, view)
    assert restored["investment_debate_state"]["history"] == history + "\nBear Analyst: 新风险。"
    assert restored["investment_debate_state"]["bull_history"] == history


def test_research_graph_reuses_market_and_runs_only_missing_fundamentals(tmp_path, monkeypatch):
    from tradingagents.graph.conditional_logic import ConditionalLogic
    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.setup import GraphSetup

    calls = []
    class LocalModel:
        def invoke(self, messages, **kwargs):
            calls.append(messages)
            return AIMessage(content="营收 108.25 亿元，来源为季度财报。", usage_metadata={
                "input_tokens": 500, "output_tokens": 50, "total_tokens": 550,
                "input_token_details": {"cache_read": 0}})
    def market(_llm):
        def forbidden(_state):
            raise AssertionError("valid cached market research must skip the analyst")
        return forbidden
    def fundamentals(llm):
        def node(state):
            assert resolve_model(POLICY) == llm._context().config["model_policy"]["default_model"]
            return {"fundamentals_report": llm.invoke("补充营收研究").content}
        return node
    monkeypatch.setattr("tradingagents.graph.setup.create_market_analyst", market)
    monkeypatch.setattr("tradingagents.graph.setup.create_fundamentals_analyst", fundamentals)
    for name in ["create_bull_researcher", "create_trader", "create_portfolio_manager"]:
        monkeypatch.setattr("tradingagents.graph.setup." + name, lambda *_: pytest.fail("research must skip trading stages"))
    native = LocalModel()
    graph = GraphSetup(native, native, {}, ConditionalLogic()).setup_graph(["market", "fundamentals"], template="research").compile()
    initial = Propagator().create_initial_state("600487.SH", "2026-09-30", market="cn_a")
    initial["research_reuse"] = select([receipt()])
    db = Database(tmp_path / "graph.db")
    context = AgentContext.root(POLICY, db=db, as_of_date="2026-09-30", symbols=["600487.SH"])
    with use_context(context):
        result = asyncio.run(graph.ainvoke(initial))
    assert len(calls) == 1
    assert result["market_report"] == receipt()["output"]["market_report"]
    assert "108.25" in result["fundamentals_report"]
    records = db.list_agent_runtime(context.root_id)
    reused = next(row for row in records if row["role"] == "Market Analyst")
    assert reused["reused_from"]["run_id"] == "analyst" and reused["evidence_refs"] == ["price"]
    assert reused["completed_at"] and reused["report_digest"]
    assert summarize_usage(records)["model_calls"] == 1
    assert summarize_usage(records)["input_tokens"] == 500


def test_downstream_prompt_is_bounded_while_audit_report_and_history_stay_full(tmp_path):
    long = "收盘价 30.25 元。\n风险：跌破 28.80 元需重评。\n" + "\n".join(f"背景 {i}，经营正常。" for i in range(1000))
    state = {"trade_date": "2026-09-30", "market_report": long,
             "investment_debate_state": {"history": long, "bull_history": long, "count": 2}}
    def researcher(view):
        assert estimate_tokens(view["market_report"]) < 1100
        assert "30.25" in view["market_report"] and "28.80" in view["market_report"]
        return {"investment_debate_state": {**view["investment_debate_state"],
                    "history": view["investment_debate_state"]["history"] + "\n新观点"}}
    db = Database(tmp_path / "handoff.db")
    context = AgentContext.root(POLICY, db=db)
    with use_context(context):
        result = role_node(researcher, AgentSpec("Bull Researcher")).invoke(state)
    assert state["market_report"] == long
    assert result["investment_debate_state"]["history"] == long + "\n新观点"
    record = db.list_agent_runtime(context.root_id)[0]
    assert record["output"]["investment_debate_state"]["history"] == long + "\n新观点"
    assert record["context_stats"]["saved_tokens_estimate"] > 0


def test_full_graph_runs_all_decision_and_risk_nodes_after_reusing_analysts(tmp_path, monkeypatch):
    from tradingagents.graph.conditional_logic import ConditionalLogic
    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.setup import GraphSetup

    called = []
    class LocalModel:
        def invoke(self, prompt, **kwargs):
            assert estimate_tokens(prompt) < 5000
            called.append(prompt)
            return AIMessage(content="Hold，价格 30.25 元，止损观察线 28.80 元。", usage_metadata={
                "input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050,
                "input_token_details": {"cache_read": 0}})
    long_report = "营收 108.25 亿元。\n价格 30.25 元。\n风险：跌破 28.80 元需重评。\n" + "\n".join(
        f"一般背景 {i}，公司持续经营。" for i in range(900))
    market = receipt(output={"market_report": long_report})
    fundamental = receipt(run_id="fund", role="Fundamentals Analyst", output={"fundamentals_report": long_report})
    def forbidden(_):
        return lambda _state: pytest.fail("reused analyst must not execute")
    monkeypatch.setattr("tradingagents.graph.setup.create_market_analyst", forbidden)
    monkeypatch.setattr("tradingagents.graph.setup.create_fundamentals_analyst", forbidden)
    def factory(role, debate_key=None, report_key=None):
        def create(llm):
            def node(state):
                llm.invoke(role + "\n" + state["market_report"] + "\n" + state["fundamentals_report"])
                if report_key:
                    result = {report_key: "Hold，价格 30.25 元，风险观察线 28.80 元。"}
                    if report_key == "final_trade_decision":
                        result["structured_portfolio_decision"] = {"rating": "Hold", "confidence": 0.5}
                    return result
                debate = state[debate_key]
                argument = role + ": 风险需核验。" * 600
                return {debate_key: {**debate, "history": debate["history"] + "\n" + argument,
                    "current_response": argument, "latest_speaker": role, "count": debate["count"] + 1}}
            return node
        return create
    for name, role, key, report in [
        ("bull_researcher", "Bull Analyst", "investment_debate_state", None),
        ("bear_researcher", "Bear Analyst", "investment_debate_state", None),
        ("research_manager", "Research Manager", None, "investment_plan"),
        ("trader", "Trader", None, "trader_investment_plan"),
        ("aggressive_debator", "Aggressive Analyst", "risk_debate_state", None),
        ("conservative_debator", "Conservative Analyst", "risk_debate_state", None),
        ("neutral_debator", "Neutral Analyst", "risk_debate_state", None),
        ("portfolio_manager", "Portfolio Manager", None, "final_trade_decision"),
    ]:
        monkeypatch.setattr("tradingagents.graph.setup.create_" + name, factory(role, key, report))
    graph = GraphSetup(LocalModel(), LocalModel(), {}, ConditionalLogic()).setup_graph(
        ["market", "fundamentals"], template="full").compile()
    initial = Propagator().create_initial_state("600487.SH", "2026-09-30", market="cn_a")
    initial["research_reuse"] = select([market, fundamental])
    context = AgentContext.root(POLICY, db=Database(tmp_path / "full.db"), symbols=["600487.SH"], as_of_date="2026-09-30")
    with use_context(context):
        result = asyncio.run(graph.ainvoke(initial))
    assert len(called) == 8
    assert result["structured_portfolio_decision"]["rating"] == "Hold"
    assert result["market_report"] == result["fundamentals_report"] == long_report
    assert len(result["risk_debate_state"]["history"]) > 10000
    assert summarize_usage(context.db.list_agent_runtime(context.root_id))["model_calls"] == 8
    assert sum((row.get("context_stats") or {}).get("saved_tokens_estimate", 0)
               for row in context.db.list_agent_runtime(context.root_id) if row["kind"] == "agent") > 20000


@pytest.mark.parametrize("mode,reused", [("normal", True), ("refresh", False), ("foreign", False),
                                         ("expired", False), ("failed", False), ("gap", False)])
def test_stock_skill_validates_reuse_scope_and_emits_plan_and_provenance(tmp_path, monkeypatch, mode, reused):
    import importlib
    from types import SimpleNamespace

    from tradingagents.skills.stock_analysis.skill import StockAnalysisInput, StockAnalysisSkill
    skill_module = importlib.import_module("tradingagents.skills.stock_analysis.skill")

    db = Database(tmp_path / "skill.db")
    store = AgentStore(db)
    conversation = store.create_conversation("研究", None)
    first = store.create_task(conversation["id"], "分析")
    prior = receipt(root_id=first["id"])
    if mode == "expired":
        prior["completed_at"] = (NOW - timedelta(hours=1)).isoformat()
    if mode == "failed":
        prior["status"] = "failed"
    if mode == "gap":
        prior["output"]["market_report"] = "主题数据不可用，部分指标缺失。"
    db.save_agent_runtime(prior)
    store.set_status(first["id"], "completed")
    if mode == "foreign":
        conversation = store.create_conversation("另一对话", None)
    current = store.create_task(conversation["id"], "补充基本面")
    config = {**POLICY, "db": db, "run_id": "skill-run", "research_conversation_id": conversation["id"],
              "research_task_id": current["id"]}
    temporal = SimpleNamespace(market_asof_date="2026-09-30", info_cutoff=NOW.isoformat(), to_dict=lambda: {})
    monkeypatch.setattr(skill_module, "resolve_temporal_context", lambda _cfg, _date, **kw: (temporal, kw["params"]))
    original_load = load_reusable_research
    monkeypatch.setattr(skill_module, "load_reusable_research",
                        lambda *args, **kw: original_load(*args, **kw, now=NOW))
    captured = []
    class FixtureGraph:
        def __init__(self, selected_analysts, config):
            captured.append(config["research_reuse"])
        async def astream_propagate(self, **kwargs):
            yield {"type": "context_compacted", "data": {"agent": "Research Manager", "context_stats": {
                "input_tokens_before": 10000, "input_tokens_after": 1000, "saved_tokens_estimate": 9000}}}
            yield {"type": "report_complete", "data": {"sections": {"market_report": prior["output"]["market_report"],
                "fundamentals_report": "营收 108.25 亿元。"}, "specialist_results": [
                    {"role": value["role"], "reused_from": value["source"]} for value in captured[-1].values()]}}
    monkeypatch.setattr("tradingagents.graph.trading_graph.TradingAgentsGraph", FixtureGraph)
    skill = StockAnalysisSkill()
    monkeypatch.setattr(skill, "_save_report_to_disk", lambda **_: None)
    monkeypatch.setattr(skill, "_save_analysis_artifact", lambda *_args, **_: "artifact")
    monkeypatch.setattr(skill, "_save_reflection_case", lambda *_args, **_: None)
    async def run():
        context = AgentContext.root(config, root_id=current["id"], db=db, symbols=["600487.SH"])
        with use_context(context):
            return [event async for event in skill.execute(StockAnalysisInput(ticker="600487.SH",
                analysis_date="2026-09-30", analysts=["market", "fundamentals"],
                analysis_template="research", force_refresh=mode == "refresh"), config)]
    events = asyncio.run(run())
    assert bool(captured[0]) == reused
    plan = next(event.data for event in events if event.event_type == "skill_progress" and event.data.get("stage_id") == "research_scope")
    assert "本轮获取" in plan["detail"] and "基本面" in plan["detail"]
    assert "保留事实" in next(event.data["detail"] for event in events if event.data.get("stage_label") == "整理研究摘要")
    final = next(event.data for event in events if event.event_type == "skill_complete")
    assert final["research_reuse"]["reused"] == (["技术面"] if reused else [])
    assert "基本面" in final["research_reuse"]["fresh"]


def test_model_receives_structured_report_digests_without_losing_mid_report_numbers():
    from tradingagents.core.agent_harness import _answer_evidence_result, _bounded_tool_context

    report = "\n".join(f"背景信息 {i}，公司正常经营。" for i in range(400))
    report += "\n营收 108.25 亿元，同比增长 12.34%。\n风险：现金流 -1.25 亿元。\n行业对比缺失。"
    rows = []
    for key, (_, role, _) in REPORT_ROLES.items():
        rows.append({"role": role, "run_id": role, "status": "completed", "model": "qwen3.8-max",
                     "output": {key: report}, "report_digest": report_digest(report),
                     "tool_observations": [{"tool": "get_income_statement", "status": "completed", "result_excerpt": "营收 108.25 亿元"}]})
    payload = _answer_evidence_result({"tool_name": "skill", "result": {"run_id": "workflow", "as_of_date": "2026-09-30",
        "result": {"ticker": "600487.SH", "structured_conclusion": {"rating": "Research"}, "specialist_results": rows}}})
    bounded = json.loads(_bounded_tool_context(payload, limit=16000))
    assert "result" in bounded and "excerpt" not in bounded
    for specialist in bounded["result"]["specialist_results"]:
        text = json.dumps(specialist["report_digests"], ensure_ascii=False)
        assert "108.25" in text and "12.34%" in text and "-1.25" in text
        assert "行业对比缺失" in text


def test_research_expiring_after_selection_is_fetched_again(tmp_path):
    cached = select([receipt()])
    cached["market_report"]["source"]["completed_at"] = (NOW - timedelta(hours=1)).isoformat()
    called = []
    def fetch(_state):
        called.append(True)
        return {"market_report": "本轮重新取得价格 31.25 元。"}
    context = AgentContext.root(POLICY, db=Database(tmp_path / "expired.db"), symbols=["600487.SH"], as_of_date="2026-09-30")
    with use_context(context):
        result = role_node(fetch, AgentSpec("Market Analyst", report_key="market_report")).invoke({
            "company_of_interest": "600487.SH", "trade_date": "2026-09-30", "research_reuse": cached})
    assert called and "31.25" in result["market_report"]
    record = next(row for row in context.db.list_agent_runtime(context.root_id) if row["kind"] == "agent")
    assert record["reused_from"] is None and "本轮重新获取数据" in record["warnings"][0]


def test_holding_or_selection_context_cannot_reuse_or_export_generic_research(tmp_path):
    from tradingagents.core.agent_runtime import bind_scope

    cached = select([receipt()])
    context = AgentContext.root(POLICY, db=Database(tmp_path / "context.db"), symbols=["600487.SH"], as_of_date="2026-09-30")
    with use_context(context):
        bind_scope(research_reuse_allowed=False)
        result = role_node(lambda _: {"market_report": "结合用户成本 32 元，重新核对持仓风险。"},
                           AgentSpec("Market Analyst", report_key="market_report")).invoke({
            "company_of_interest": "600487.SH", "trade_date": "2026-09-30", "research_reuse": cached})
    assert "用户成本" in result["market_report"]
    record = next(row for row in context.db.list_agent_runtime(context.root_id) if row["kind"] == "agent")
    assert record["research_policy_key"] is None and record["reused_from"] is None
