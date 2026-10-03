"""Unified runtime contracts, using fixed local tools/models only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from tradingagents.core.agent_runtime import (
    AgentContext,
    AgentSession,
    AgentSpec,
    RuntimeLimitError,
    ScopeError,
    TaskBudget,
    ToolExecutor,
    agent_run,
    current_context,
    runtime_model,
    use_context,
)
from tradingagents.core.model_policy import config_update, freeze_model_config, resolve_model
from tradingagents.core.persistence import Database


def test_policy_migrates_legacy_and_new_policy_wins():
    legacy = {"quick_think_llm": "flash", "deep_think_llm": "pro", "agent_model": "chat"}
    snapshot = freeze_model_config(legacy)
    assert resolve_model(snapshot) == "chat"
    assert resolve_model(snapshot, "deep") == "pro"
    updates = config_update(legacy, {"model_policy": {"default_model": "unified"}})
    assert updates["agent_model"] == updates["quick_think_llm"] == updates["deep_think_llm"] == "unified"
    legacy["agent_model"] = "later"
    assert resolve_model(snapshot) == "chat"


def test_shared_budget_is_atomic_across_parallel_children():
    budget = TaskBudget(max_model_calls=7)
    def consume(_):
        try:
            budget.consume("model")
            return True
        except RuntimeLimitError:
            return False
    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(consume, range(50))) == 7
    assert budget.snapshot()["model_calls"] == 7


def test_child_cannot_expand_account_symbol_date_and_shares_cancellation():
    context = AgentContext.root({}, account_id="paper:a", symbols=["600519.SH"], as_of_date="2026-09-29")
    child = context.child(symbols=["600519.SS"])
    assert child.budget is context.budget
    for kwargs in ({"symbols": ["AAPL"]}, {"account_id": "paper:b"}, {"as_of_date": "2026-09-30"}):
        with pytest.raises(ScopeError):
            context.child(**kwargs)
    context.budget.cancelled.set()
    with pytest.raises(RuntimeLimitError):
        child.budget.consume("tool")


@tool
def price(symbol: str, end_date: str) -> dict:
    """Read a fixed price fixture."""
    return {"symbol": symbol, "date": end_date, "price": 10}


def test_native_session_executes_tools_and_rejects_out_of_scope():
    class Model:
        def __init__(self):
            self.n = 0
        async def ainvoke(self, messages):
            self.n += 1
            if self.n == 1:
                return AIMessage(content="", tool_calls=[{"id": "p", "name": "price", "args": {"symbol": "AAPL", "end_date": "2026-09-29"}}])
            assert "超出标的范围" in messages[-1].content
            return AIMessage(content="没有权限读取该标的")
    async def run():
        context = AgentContext.root({}, symbols=["600519.SH"], as_of_date="2026-09-29")
        with use_context(context):
            session = AgentSession(runtime_model(Model(), "Analyst"), "goal", "system", {"price"}, 1)
            result = await session.run(ToolExecutor([price]))
            assert result.content == "没有权限读取该标的"
            assert context.budget.tool_calls == 0
    asyncio.run(run())


def test_valid_tools_and_typed_child_result_persist_without_secrets(tmp_path):
    db = Database(tmp_path / "runtime.db")
    context = AgentContext.root({"model_policy": {"default_model": "flash"}, "api_key": "secret"}, root_id="task", db=db)
    async def run():
        with use_context(context), agent_run(AgentSpec("Stock workflow")) as workflow, agent_run(AgentSpec("News Analyst")) as child:
            result = await ToolExecutor([price]).execute("price", {"symbol": "AAPL", "end_date": "2026-09-29"})
            child.output = {"fact": result["price"], "rating": "observe"}
            assert current_context().parent_id == workflow.run_id
        rows = db.list_agent_runtime("task")
        assert len(rows) == 3
        assert all(row["status"] == "completed" for row in rows)
        assert next(row for row in rows if row["role"] == "News Analyst")["output"] == {"fact": 10, "rating": "observe"}
        assert "secret" not in str(rows)
    asyncio.run(run())


def test_no_more_model_calls_after_cancel_or_budget_limit():
    class Model:
        async def ainvoke(self, messages):
            return AIMessage(content="done")
    async def run():
        context = AgentContext.root({})
        context.budget.max_model_calls = 1
        with use_context(context):
            model = runtime_model(Model(), "Researcher")
            await model.ainvoke([])
            with pytest.raises(RuntimeLimitError):
                await model.ainvoke([])
    asyncio.run(run())


def test_tool_failures_and_cancellation_have_terminal_records(tmp_path):
    from tradingagents.core.tool_registry import LightweightTool
    async def failing():
        raise OSError("provider unavailable")
    async def cancelled():
        raise asyncio.CancelledError()
    db = Database(tmp_path / "failures.db")
    context = AgentContext.root({}, root_id="tools", db=db)
    async def run():
        with use_context(context):
            for name, handler, exception in [("failing", failing, OSError), ("cancelled", cancelled, asyncio.CancelledError)]:
                executor = ToolExecutor([LightweightTool(name=name, description=name, parameters={}, handler=handler)])
                with pytest.raises(exception):
                    await executor.execute(name, {})
        records = {row["role"]: row for row in db.list_agent_runtime("tools")}
        assert records["tool:failing"]["status"] == "failed"
        assert records["tool:cancelled"]["status"] == "cancelled"
        assert records["tool:failing"]["output"] == {"error_type": "OSError"}
    asyncio.run(run())


@pytest.mark.parametrize("template,expected_roles", [
    ("research", ["Market Analyst"]),
    ("full", ["Market Analyst", "Bull Researcher", "Bear Researcher", "Research Manager", "Trader",
              "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager"]),
])
def test_workflow_templates_preserve_order_and_typed_results(monkeypatch, tmp_path, template, expected_roles):
    import tradingagents.graph.setup as setup
    from tradingagents.graph.conditional_logic import ConditionalLogic
    from tradingagents.graph.propagation import Propagator

    order = []
    def factory(name):
        def make(_llm):
            def node(state):
                order.append(name)
                result = {"messages": [AIMessage(content=name)]}
                if name == "Market Analyst":
                    result["market_report"] = "价格已核实"
                elif name in {"Bull Researcher", "Bear Researcher"}:
                    debate = dict(state["investment_debate_state"])
                    debate.update(count=debate["count"] + 1, current_response=name)
                    result["investment_debate_state"] = debate
                elif name.endswith("Analyst"):
                    risk = dict(state["risk_debate_state"])
                    risk.update(count=risk["count"] + 1, latest_speaker=name)
                    result["risk_debate_state"] = risk
                elif name == "Portfolio Manager":
                    result["structured_portfolio_decision"] = {"rating": "HOLD", "confidence": "low"}
                    result["final_trade_decision"] = "HOLD"
                return result
            return node
        return make
    for name, func in [("Market Analyst", "create_market_analyst"), ("Bull Researcher", "create_bull_researcher"),
                       ("Bear Researcher", "create_bear_researcher"), ("Research Manager", "create_research_manager"),
                       ("Trader", "create_trader"), ("Aggressive Analyst", "create_aggressive_debator"),
                       ("Neutral Analyst", "create_neutral_debator"), ("Conservative Analyst", "create_conservative_debator"),
                       ("Portfolio Manager", "create_portfolio_manager")]:
        monkeypatch.setattr(setup, func, factory(name))
    from langgraph.prebuilt import ToolNode
    graph = setup.GraphSetup(object(), object(), {"market": ToolNode([])}, ConditionalLogic()).setup_graph(["market"], template).compile()
    db = Database(tmp_path / "template.db")
    context = AgentContext.root({}, root_id="workflow", db=db, symbols=["AAPL"], as_of_date="2026-09-29")
    initial = Propagator().create_initial_state("AAPL", "2026-09-29")
    with use_context(context):
        result = graph.invoke(initial)
    assert order == expected_roles
    rows = db.list_agent_runtime("workflow")
    assert [row["role"] for row in rows] == expected_roles
    assert all(row["status"] == "completed" and row["symbols"] == ["AAPL"] for row in rows)
    if template == "full":
        assert result["structured_portfolio_decision"]["rating"] == "HOLD"
        assert rows[-1]["output"]["structured_portfolio_decision"]["rating"] == "HOLD"
    else:
        assert result["structured_portfolio_decision"] is None


def test_actual_analyst_uses_shared_tool_loop_in_sync_and_async_graphs():
    from tradingagents.agents.utils.create_tool_analyst import create_tool_analyst
    from tradingagents.graph.propagation import Propagator
    class Model:
        def bind_tools(self, _tools):
            return self
        async def ainvoke(self, messages):
            if messages[-1].type == "tool":
                assert '"price": 10' in messages[-1].content
                return AIMessage(content="核实价格为 10")
            return AIMessage(content="", tool_calls=[{"id": "price-1", "name": "price", "args": {"symbol": "AAPL", "end_date": "2026-09-29"}}])
    node = create_tool_analyst(runtime_model(Model(), "Market Analyst"), [price], "Analyze price", "market_report")
    state = Propagator().create_initial_state("AAPL", "2026-09-29")
    assert node.invoke(state)["market_report"] == "核实价格为 10"
    assert asyncio.run(node.ainvoke(state))["market_report"] == "核实价格为 10"


def test_restart_reconciles_child_records_and_preserves_existing_runs(tmp_path):
    db = Database(tmp_path / 'existing.db')
    db.save_run('old-run', 'stock_analysis', {}, 'completed')
    db.save_agent_runtime({'run_id': 'abandoned', 'root_id': 'root', 'parent_id': 'root',
                           'status': 'running', 'model': 'flash', 'role': 'Market Analyst',
                           'host_run_id': 'analysis'})
    reopened = Database(tmp_path / 'existing.db')
    assert reopened.reconcile_agent_runtime() == 1
    assert reopened.list_agent_runtime('analysis')[0]['status'] == 'interrupted'
    assert reopened.get_run('old-run')['status'] == 'completed'


def test_multi_symbol_and_compact_date_scope():
    context = AgentContext.root({}, symbols=['AAPL'], as_of_date='2026-09-29')
    executor = ToolExecutor([price], context=context)
    executor.validate('price', {'symbol': 'AAPL', 'end_date': '20260929'})
    with pytest.raises(ScopeError):
        executor.validate('price', {'ts_codes': ['AAPL', 'NVDA']})
    with pytest.raises(ScopeError):
        executor.validate('price', {'symbol': 'AAPL', 'end_date': '20260930'})


def test_inline_stream_scopes_are_isolated_and_early_close_does_not_cancel_sibling(tmp_path):
    from tradingagents.core.agent_runtime import bind_scope, managed_stream
    from tradingagents.skills.base import SkillEvent
    async def source(symbol):
        bind_scope(symbols=[symbol], memory_refs=[f'memory:{symbol}'])
        yield SkillEvent(event_type='skill_start', data={'ticker': symbol})
        yield SkillEvent(event_type='skill_complete', data={'status': 'success', 'ticker': symbol})
    async def run():
        context = AgentContext.root({}, db=Database(tmp_path / 'streams.db'), root_id='parent')
        with use_context(context):
            first = managed_stream(AgentSpec('stock_analysis'), source('AAPL'), {})
            await first.__anext__()
            assert current_context().symbols == frozenset()
            await first.aclose()
            second = managed_stream(AgentSpec('stock_analysis'), source('NVDA'), {})
            events = [event async for event in second]
            assert events[-1].data['ticker'] == 'NVDA'
            assert current_context().memory_refs == ()
        rows = context.db.list_agent_runtime('parent')
        assert [row['status'] for row in rows] == ['cancelled', 'completed']
        assert rows[0]['memory_refs'] == ['memory:AAPL']
        assert rows[1]['memory_refs'] == ['memory:NVDA']
        assert not context.budget.cancelled.is_set()
    asyncio.run(run())


def test_specialists_receive_shared_memory_and_only_supplied_ids_are_recorded(tmp_path):
    from tradingagents.core.agent_runtime import bind_scope
    class Model:
        async def ainvoke(self, messages):
            assert 'shared_strategy_memory' in messages[0].content
            assert 'lesson-approved' in messages[0].content
            return {'memory_usage': [{'lesson_id': 'lesson-approved', 'status': 'referenced', 'reason': '同一风险条件'},
                                     {'lesson_id': 'invented', 'status': 'referenced', 'reason': '不可引用'}]}
    async def run():
        context = AgentContext.root({}, db=Database(tmp_path / 'memory.db'), root_id='task')
        with use_context(context):
            bind_scope(memory_refs=['lesson-approved'], memory_prompt='历史事实 lesson-approved，不能替代当前行情')
            for role in ['News Analyst', 'Fundamentals Analyst', 'Research Manager']:
                await runtime_model(Model(), role).ainvoke([AIMessage(content='当前研究')])
        rows = context.db.list_agent_runtime('task')
        assert len(rows) == 3
        assert all(row['memory_refs'] == ['lesson-approved'] for row in rows)
        assert all([u['lesson_id'] for u in row['output']['memory_usage']] == ['lesson-approved'] for row in rows)
    asyncio.run(run())


def test_role_handoff_preserves_tool_receipts_across_async_contexts(tmp_path):
    import json

    from tradingagents.core.agent_harness import _answer_evidence_result, _NativeToolSession
    from tradingagents.core.agent_runtime import role_node
    from tradingagents.core.tool_registry import LightweightTool

    db = Database(tmp_path / 'handoff.db')
    context = AgentContext.root({}, root_id='handoff', db=db, as_of_date='2026-09-30')

    async def announcements():
        return '# Announcements (2026-06-01 -> 2026-09-30)\n# Rows: 25\n2026-09-18,累计诉讼、仲裁事项的公告'

    async def missing_prices():
        raise OSError('provider unavailable')

    async def analyst(_state):
        executor = ToolExecutor([
            LightweightTool(name='get_announcements', description='', parameters={}, handler=announcements),
            LightweightTool(name='get_stock_data', description='', parameters={}, handler=missing_prices),
        ])
        await executor.execute('get_announcements', {})
        with pytest.raises(OSError):
            await executor.execute('get_stock_data', {})
        return {'fundamentals_report': '行情不可用\n' + '报告详细分析。' * 700 + '\n结论：已取得财报和诉讼公告，价格趋势尚待核实。'}

    async def run():
        with use_context(context):
            result = await role_node(analyst, AgentSpec('Fundamentals Analyst')).ainvoke({})
        specialist = result['specialist_results'][0]
        receipts = {item['tool']: item for item in specialist['tool_observations']}
        assert receipts['get_announcements']['status'] == 'completed'
        assert receipts['get_stock_data']['status'] == 'failed'
        assert receipts['get_stock_data']['error_type'] == 'OSError'
        assert receipts['get_announcements']['run_id'] in specialist['evidence_refs']
        assert receipts['get_announcements']['run_id'] in result['agent_evidence_refs']
        projected = _answer_evidence_result({'tool_name': 'skill', 'result': {
            'run_id': 'run', 'source': 'stock_analysis', 'as_of_date': '2026-09-30',
            'result': {'ticker': '600667.SH', 'structured_conclusion': {'rating': 'Research'},
                       'specialist_results': result['specialist_results']},
        }})
        compact = projected['result']['specialist_results'][0]
        assert '已取得财报和诉讼公告' in compact['report_excerpts']['fundamentals_report']
        assert compact['reports_partial'] is True
        assert '累计诉讼' in compact['tool_observations'][0]['result_excerpt']
        session = _NativeToolSession(None, 'goal', 'system', {'stock_analysis'}, 1)
        session.tool_result('call', 'stock_analysis', projected)
        encoded = json.loads(session.messages[-1].content)
        assert 'result' in encoded and 'excerpt' not in encoded
        assert next(row for row in db.list_agent_runtime('handoff') if row['kind'] == 'agent')['tool_observations'] == specialist['tool_observations']

    asyncio.run(run())


def test_sequential_tool_rounds_reserve_report_with_existing_evidence():
    """Qwen may collect each indicator in a separate model turn."""
    class Collector:
        n = 0
        async def ainvoke(self, messages):
            self.n += 1
            return AIMessage(content="", tool_calls=[{
                "id": f"p-{self.n}", "name": "price",
                "args": {"symbol": "600487.SH", "end_date": "2026-09-30"},
            }])
    class Reporter:
        async def ainvoke(self, messages):
            observations = [m for m in messages if m.type == "tool"]
            assert len(observations) == 11
            assert all('"price": 10' in m.content for m in observations)
            assert "停止调用工具" in messages[-1].content
            return AIMessage(content="截至9月30日，价格10；辅助数据缺失，不能判断题材热度。")
    async def run():
        context = AgentContext.root({}, symbols=["600487.SH"])
        collector = Collector()
        with use_context(context):
            session = AgentSession(runtime_model(collector, "Market Analyst"), "goal", "system",
                                   {"price"}, 1, final_llm=runtime_model(Reporter(), "Market Analyst"))
            result = await session.run(ToolExecutor([price]))
            assert "价格10" in result.content
            assert collector.n == 11
            assert context.budget.model_calls == 12
            assert context.budget.tool_calls == 11
    asyncio.run(run())


def test_report_closing_turn_respects_shared_cancellation():
    class Collector:
        async def ainvoke(self, messages):
            return AIMessage(content="", tool_calls=[{
                "id": "p", "name": "price", "args": {"symbol": "600487.SH", "end_date": "2026-09-30"},
            }])
    class Reporter:
        async def ainvoke(self, messages):
            raise AssertionError("cancelled task must not call model")
    async def run():
        context = AgentContext.root({})
        class CancellingExecutor(ToolExecutor):
            async def execute(self, name, args):
                result = await super().execute(name, args)
                context.budget.cancelled.set()
                return result
        with use_context(context):
            session = AgentSession(runtime_model(Collector(), "Market Analyst"), "goal", "system",
                                   {"price"}, 1, max_rounds=2,
                                   final_llm=runtime_model(Reporter(), "Market Analyst"))
            with pytest.raises(RuntimeLimitError, match="任务已取消"):
                await session.run(CancellingExecutor([price]))
            assert context.budget.model_calls == 1
    asyncio.run(run())
