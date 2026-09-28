"""Durable Agent task lifecycle and account evidence boundaries."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from tradingagents.core.agent_harness import (
    AgentStore,
    TradingAgentHarness,
    _answer_evidence_result,
    _paper_state_fingerprint,
)
from tradingagents.core.chat_agent import ChatResponse
from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager, RunStatus
from tradingagents.core.stockmanager_paper import PaperServiceError
from tradingagents.core.tool_registry import LightweightTool, ToolRegistry
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


class _Chat:
    def __init__(self, response=None):
        self.response = response or ChatResponse(intent="chat_answer", content="普通回答")

    async def handle(self, *_args, **_kwargs):
        return self.response


class _Skills:
    def get(self, _name):
        raise AssertionError("写入型 Skill 不得启动")


def test_concurrent_submissions_keep_one_active_task_per_conversation(tmp_path):
    database = Database(tmp_path / "agent.db")
    first_store = AgentStore(database)
    second_store = AgentStore(database)
    conversation = first_store.create_conversation("并发任务", None)
    barrier = Barrier(2)

    def create(store, goal):
        barrier.wait(timeout=5)
        try:
            return store.create_task(conversation["id"], goal)["id"]
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create, first_store, "分析甲")
        second = pool.submit(create, second_store, "分析乙")
        results = [first.result(timeout=10), second.result(timeout=10)]

    assert len(first_store.list_tasks(conversation["id"])) == 1
    assert sum(result == "当前对话已有运行中的任务" for result in results) == 1


def test_concurrent_event_writers_keep_contiguous_replay_sequence(tmp_path):
    database = Database(tmp_path / "agent.db")
    stores = [AgentStore(database), AgentStore(database)]
    conversation = stores[0].create_conversation("事件重放", None)
    task = stores[0].create_task(conversation["id"], "分析")
    barrier = Barrier(2)

    def write_events(store, writer):
        barrier.wait(timeout=5)
        for index in range(20):
            store.event(task["id"], "test_event", {"writer": writer, "index": index})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write_events, stores[0], "first")
        second = pool.submit(write_events, stores[1], "second")
        first.result(timeout=15)
        second.result(timeout=15)

    events = stores[0].list_events(task["id"])
    assert [event["seq"] for event in events] == list(range(1, 41))
    assert {event["payload"]["writer"] for event in events} == {"first", "second"}


def test_stock_reference_only_applies_to_stock_followups():
    assert TradingAgentHarness._is_stock_followup("那它的公告呢？")
    assert TradingAgentHarness._is_stock_followup("这只股票怎么样？")
    assert not TradingAgentHarness._is_stock_followup("解释计划为何没有执行它")


def test_trade_decision_detection_keeps_general_education_separate():
    assert TradingAgentHarness._asks_trade_decision("现在要不要买入茅台？")
    assert TradingAgentHarness._asks_trade_decision("600519.SH 适合加仓吗？")
    assert TradingAgentHarness._asks_trade_decision("给我一个买入计划")
    assert not TradingAgentHarness._asks_trade_decision("如何制定买入纪律？")
    assert not TradingAgentHarness._asks_trade_decision("帮我介绍买卖策略")
    assert TradingAgentHarness._needs_factor("600519.SH 公告后要不要卖出？")
    assert not TradingAgentHarness._needs_factor("查看 600519.SH 的公告")
    assert TradingAgentHarness._asks_trade_execution_feasibility("600519.SH 现在还能卖出吗？")
    assert not TradingAgentHarness._asks_trade_execution_feasibility("解释卖出纪律")


def test_artifact_search_requires_an_existing_report_reference(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id}

    harness, _ = _harness(tmp_path, paper_handler=paper)
    current = "当前模拟盘账本的权益和现金分别是多少？只报告账本事实。"
    assert [step["tool"] for step in harness._plan(current, "paper:mine")] == [
        "get_paper_session",
    ]
    assert [step["tool"] for step in harness._validate_model_steps(
        [{"tool": "search_artifacts", "query": "所有报告"}], current, "paper:mine",
    )] == ["get_paper_session"]
    assert not harness._asks_artifacts("分析当前账户风险并说明依据")
    assert harness._asks_artifacts("查看上次的策略报告")
    assert harness._asks_artifacts("对比上次的回测")
    assert [step["tool"] for step in harness._plan(
        "查看上次的策略报告", "paper:mine",
    )] == ["get_paper_session", "search_artifacts"]


@pytest.mark.asyncio
async def test_unscoped_trade_decision_asks_for_symbol_before_legacy_chat(tmp_path):
    class UnusedChat:
        async def handle(self, *_args, **_kwargs):
            raise AssertionError("没有标的和账户的交易判断不得走旧聊天路由")

    async def unused_paper(session_id):
        raise AssertionError(f"没有绑定模拟盘时不得读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper, chat=UnusedChat())
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "现在要不要买入茅台？")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert detail["status"] == "needs_input"
    assert "A 股代码" in detail["result"]["content"]
    assert detail["evidence"] == []
    assert detail["events"][-1]["event_type"] == "task_needs_input"


@pytest.mark.asyncio
async def test_code_reply_continues_previous_trade_decision_with_fresh_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("tradingagents.core.trading_time.get_temporal_context",
                        lambda *_args, **_kwargs: SimpleNamespace(market_asof_date="2026-09-25"))
    async def unused_paper(session_id):
        raise AssertionError(f"没有绑定模拟盘时不得读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False
    calls = []

    async def factor(ts_code):
        calls.append(ts_code)
        return {"source": "StockManager MCP", "ts_code": ts_code,
                "as_of_date": "2026-09-25", "snapshot": {"rows": []}}

    async def synthesize(goal, _conversation_id, _evidence, **_kwargs):
        assert "要不要买入" in goal
        assert "600519.SH" in goal
        return "已根据新取的证据评估。"

    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={}, handler=factor,
    ))
    harness._synthesize = synthesize
    conversation = store.create_conversation("测试", None)
    first = harness.submit(conversation["id"], "现在要不要买入茅台？")
    await harness._active[first["id"]]
    second = harness.submit(conversation["id"], "600519.SH")
    await harness._active[second["id"]]

    detail = store.conversation_detail(conversation["id"])
    resumed = detail["tasks"][1]
    assert calls == ["600519.SH"]
    assert resumed["status"] == "completed"
    assert resumed["result"]["content"] == "已根据新取的证据评估。"
    assert detail["messages"][-2]["content"] == "600519.SH"
    assert any(event["event_type"] == "scope_resolved" and
               event["payload"]["previous_task_id"] == first["id"]
               for event in resumed["events"])
    assert "原交易问题" in resumed["goal"]


@pytest.mark.asyncio
async def test_manual_holdings_cannot_support_current_trade_decision(tmp_path, monkeypatch):
    monkeypatch.setattr("tradingagents.core.trading_time.get_temporal_context",
                        lambda *_args, **_kwargs: SimpleNamespace(market_asof_date="2026-09-25"))
    async def unused_paper(session_id):
        raise AssertionError(f"没有绑定模拟盘时不得读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False

    async def portfolio():
        return {"source": "TradingAgents local holdings", "holdings": [
            {"symbol": "600519.SH", "quantity": 40, "current_price": 1500,
             "record_updated_at": "2026-09-20"}],
            "warnings": ["持仓价格为本地保存值，未核对实时行情或价格时点"]}

    async def factor(ts_code):
        return {"source": "StockManager MCP", "ts_code": ts_code,
                "as_of_date": "2026-09-25", "snapshot": {"rows": []}}

    harness.tools.register(LightweightTool(
        name="get_portfolio_summary", description="portfolio", parameters={}, handler=portfolio,
    ))
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={}, handler=factor,
    ))
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "我持有 600519.SH，现在要不要加仓？")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert detail["status"] == "completed"
    assert [item["tool_name"] for item in detail["evidence"]] == [
        "get_portfolio_summary", "get_mcp_factor_snapshot",
    ]
    assert "尚未与交易账户及当前行情核对" in detail["result"]["content"]
    assert "没有生成交易判断" in detail["result"]["content"]


def test_missing_factor_score_is_masked_only_in_model_input():
    result = {"snapshot": {"rows": [{"data_coverage": {"valuation": "available", "flow": "missing"},
                                    "factor_scores": {"valuation": 52.0, "flow": 50.0}}]}}
    safe = _answer_evidence_result({"tool_name": "get_mcp_factor_snapshot", "result": result})
    assert safe["snapshot"]["rows"][0]["factor_scores"] == {"valuation": 52.0, "flow": None}
    assert result["snapshot"]["rows"][0]["factor_scores"]["flow"] == 50.0


def test_unusable_paper_plan_is_masked_only_in_model_input():
    plan = {"signal_date": "2026-09-24", "items": [{"code": "600519.SH", "action": "BUY"}]}
    result = {"session_id": "paper:mine", "snapshot": {"equity": 120000},
              "next_plan": plan, "readiness": {"can_reference_plan": False}}
    safe = _answer_evidence_result({"tool_name": "get_paper_session", "result": result})
    assert safe["snapshot"] == {"equity": 120000}
    assert safe["next_plan"] is None
    assert "不得据此提出交易建议" in safe["plan_interpretation"]
    assert result["next_plan"] == plan

    stale = {"next_plan": plan, "freshness": {"is_active_plan_current": False}}
    assert _answer_evidence_result({"tool_name": "get_paper_session", "result": stale})[
        "next_plan"] is None
    current = {"next_plan": plan, "readiness": {"can_reference_plan": True},
               "freshness": {"is_active_plan_current": True}}
    assert _answer_evidence_result({"tool_name": "get_paper_session", "result": current})[
        "next_plan"] == plan


def test_paper_model_input_preserves_account_facts_without_internal_ledger_fields():
    result = {
        "session_id": "paper:mine", "as_of_date": "2026-09-25",
        "state_fingerprint": "a" * 64,
        "session": {"strategy": "demo", "initial_cash": 100000,
                    "last_date": "2026-09-25", "params": {"private": "internal"}},
        "snapshot": {"as_of_date": "2026-09-25", "equity": 101000,
                     "cash": 50000, "updated_at": "internal", "positions": {
                         "600519.SH": {"shares": 10, "value": 51000,
                                       "pending_stock": 5, "last_price": 5100},
                     }},
        "trades_count": 1,
        "recent_trades": [{"trade_date": "2026-09-24", "code": "600519.SH",
                           "side": "BUY", "shares": 10, "source": "paper",
                           "note": "internal rebalance state"}],
        "equity_tail": [{"date": "2026-09-25", "equity": 101000,
                         "debug": "internal"}],
    }
    projected = _answer_evidence_result({"tool_name": "get_paper_session", "result": result})
    assert projected["snapshot"]["equity"] == 101000
    assert projected["snapshot"]["position_count"] == 1
    assert projected["trades_count"] == 1
    assert projected["recent_trades"][0]["source"] == "paper"
    assert "internal" not in json.dumps(projected)
    assert "state_fingerprint" not in projected
    assert result["snapshot"]["positions"]["600519.SH"]["pending_stock"] == 5


def _harness(tmp_path, *, paper_handler, chat=None):
    db = Database(tmp_path / "agent.db")
    store = AgentStore(db)
    tools = ToolRegistry()
    tools.register(LightweightTool(
        name="get_paper_session", description="paper", parameters={},
        handler=paper_handler,
    ))
    return TradingAgentHarness(store, tools, chat or _Chat(), None, _Skills(), {}), store


@pytest.mark.asyncio
async def test_read_tool_contract_blocks_unbound_scope_and_invalid_output(tmp_path):
    calls = []

    async def handler(**args):
        calls.append(args)
        return {"ts_code": args.get("ts_code", "600519.SH")}

    harness, _ = _harness(tmp_path, paper_handler=handler)
    harness.tools.register(LightweightTool(
        name="paper_probe", description="paper", parameters={"type": "object"},
        handler=handler, scope="paper",
    ))
    harness.tools.register(LightweightTool(
        name="compute_probe", description="compute", parameters={"type": "object"},
        handler=handler, permission="compute",
    ))
    harness.tools.register(LightweightTool(
        name="symbol_probe", description="symbol",
        parameters={"type": "object", "properties": {"ts_code": {"type": "string"}},
                    "required": ["ts_code"]},
        output_schema={"type": "object", "required": ["snapshot"],
                       "properties": {"snapshot": {"type": "object"}}},
        handler=handler, scope="symbol",
    ))

    wrong_account = await harness._call_read_tool(
        "paper_probe", {"session_id": "paper:other"}, "paper:mine", [],
    )
    forbidden = await harness._call_read_tool("compute_probe", {}, None, [])
    wrong_symbol = await harness._call_read_tool(
        "symbol_probe", {"ts_code": "000001.SZ"}, None, ["600519.SH"],
    )
    bad_input = await harness._call_read_tool(
        "symbol_probe", {"ts_code": 600519}, None, [600519],
    )
    assert not calls
    assert "账户范围" in wrong_account["error"]
    assert "只读交易任务" in forbidden["error"]
    assert "标的范围" in wrong_symbol["error"]
    assert "输入不符合登记契约" in bad_input["error"]

    malformed = await harness._call_read_tool(
        "symbol_probe", {"ts_code": "600519.SH"}, None, ["600519.SH"],
    )
    assert calls == [{"ts_code": "600519.SH"}]
    assert "结果不符合登记契约" in malformed["error"]


@pytest.mark.asyncio
async def test_read_tool_timeout_has_visible_reason(tmp_path):
    async def slow():
        await asyncio.sleep(1)
        return {"ok": True}

    harness, _ = _harness(tmp_path, paper_handler=slow)
    harness.tools.register(LightweightTool(
        name="slow_probe", description="slow", parameters={"type": "object"},
        handler=slow, timeout_seconds=0.01,
    ))
    result = await harness._call_read_tool("slow_probe", {}, None, [])
    assert "超过 0.01 秒" in result["error"]
    assert "工具执行超时" in result["warnings"]


@pytest.mark.asyncio
async def test_paper_task_persists_events_and_evidence_across_store_reopen(tmp_path):
    calls = []

    async def paper(session_id):
        calls.append(session_id)
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25", "snapshot": {
                    "equity": 1000000, "cash": 500000, "positions": {"600519.SH": {}}},
                "recent_trades": []}

    harness, store = _harness(tmp_path, paper_handler=paper)

    async def synthesize(*_args, **_kwargs):
        return "截至 2026-09-25，权益 100 万元。"

    harness._synthesize = synthesize
    conversation = store.create_conversation("模拟盘复核", "paper-1")
    task = harness.submit(conversation["id"], "看看当前持仓风险")
    await harness._active[task["id"]]

    reopened = AgentStore(Database(tmp_path / "agent.db"))
    detail = reopened.conversation_detail(conversation["id"])
    assert calls == ["paper-1"]
    assert detail["tasks"][0]["status"] == "completed"
    assert [item["event_type"] for item in detail["tasks"][0]["events"]] == [
        "task_created", "plan_created", "step_started", "evidence_added",
        "step_completed", "review_started", "task_completed",
    ]
    assert detail["tasks"][0]["evidence"][0]["as_of_date"] == "2026-09-25"
    assert detail["messages"][-1]["content"].startswith("截至")
    assert reopened.list_events(task["id"], after_seq=4)[0]["seq"] == 5


@pytest.mark.asyncio
async def test_unavailable_paper_source_does_not_generate_trade_advice(tmp_path):
    async def unavailable(session_id):
        return {"error": "StockManager 未连接", "warnings": ["账本不可用"]}

    harness, store = _harness(tmp_path, paper_handler=unavailable)
    conversation = store.create_conversation("测试", "paper-2")
    task = harness.submit(conversation["id"], "要不要加仓？")
    await harness._active[task["id"]]
    result = store.get_task(task["id"])["result"]
    assert "无法核对" in result["content"]
    assert "没有生成交易判断" in result["content"]
    assert result["read_only"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("ledger, goal, expected", [
    ({"session_id": "paper:mine", "snapshot": {"equity": 100000}},
     "评估账户风险", "账本缺少基准日"),
    ({"session_id": "paper:mine", "as_of_date": "2026-09-25",
      "snapshot": {"equity": 100000}, "readiness": {"can_reference_plan": False}},
     "解释下一交易日计划", "策略计划当前不可引用"),
    ({"session_id": "paper:mine", "as_of_date": "2026-09-25",
      "snapshot": {"equity": 100000}, "freshness": {"is_active_plan_current": False}},
     "当前策略切换的依据是什么", "策略计划当前不可引用"),
    ({"session_id": "paper:mine", "as_of_date": "2026-09-25",
      "snapshot": {"equity": 100000}, "freshness": {"is_active_plan_current": False}},
     "现在可以买么？", "策略计划当前不可引用"),
])
async def test_unknown_or_stale_paper_plan_does_not_generate_judgment(
    tmp_path, ledger, goal, expected,
):
    async def paper(session_id):
        assert session_id == "paper:mine"
        return ledger

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], goal)
    await harness._active[task["id"]]

    result = store.get_task(task["id"])["result"]
    assert expected in result["content"]
    assert "没有生成交易判断" in result["content"]


@pytest.mark.asyncio
async def test_risk_question_masks_unusable_plan_from_model_but_keeps_audit(tmp_path, monkeypatch):
    plan = {"signal_date": "2026-09-24", "items": [{"code": "600519.SH", "action": "BUY"}]}

    async def paper(session_id):
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-24", "snapshot": {"equity": 120000},
                "readiness": {"can_reference_plan": False}, "next_plan": plan}

    captured = []

    class FakeLLM:
        async def ainvoke(self, messages):
            captured.extend(messages)
            return SimpleNamespace(content="账本权益为 120000，策略计划尚不可引用。")

    monkeypatch.setattr("tradingagents.llm_clients.create_llm_client",
                        lambda **_kwargs: SimpleNamespace(get_llm=FakeLLM))
    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "这个模拟盘账户的权益和风险状态如何？")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert detail["status"] == "completed"
    assert detail["evidence"][0]["result"]["next_plan"] == plan
    model_evidence = json.loads(captured[1].content.split("证据 JSON：\n", 1)[1])
    assert model_evidence[0]["data"]["next_plan"] is None
    assert "不得据此提出交易建议" in model_evidence[0]["data"]["plan_interpretation"]
    assert "can_reference_plan=false" in captured[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("ledger, goal, expected", [
    ({"session_id": "paper:other", "as_of_date": "2026-09-25",
      "snapshot": {"equity": 100000}}, "评估模拟盘风险", "账户"),
    ({"session_id": "paper:mine", "as_of_date": "2026-09-25",
      "session": {"session_id": "paper:mine", "last_date": "2026-09-24"},
      "snapshot": {"as_of_date": "2026-09-25", "equity": 100000}},
     "推进模拟盘到 2026-09-28", "日期"),
])
async def test_conflicting_paper_ledger_abstains_and_never_proposes_action(
    tmp_path, ledger, goal, expected,
):
    async def paper(session_id):
        assert session_id == "paper:mine"
        return ledger

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], goal)
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert expected in detail["evidence"][0]["result"]["error"]
    assert "没有生成交易判断" in detail["result"]["content"]
    assert detail["proposal"] is None


@pytest.mark.asyncio
async def test_cancel_stops_task_and_rejects_concurrent_submission(tmp_path):
    started = asyncio.Event()

    async def slow_paper(session_id):
        started.set()
        await asyncio.sleep(60)
        return {"session_id": session_id}

    harness, store = _harness(tmp_path, paper_handler=slow_paper)
    harness.config["agent_task_timeout_seconds"] = 2.0
    conversation = store.create_conversation("测试", "paper-3")
    task = harness.submit(conversation["id"], "解释策略")
    await asyncio.wait_for(started.wait(), timeout=2)
    with pytest.raises(ValueError, match="运行中"):
        harness.submit(conversation["id"], "第二个问题")
    assert await harness.cancel(task["id"])
    await harness._active[task["id"]]
    assert store.get_task(task["id"])["status"] == "cancelled"
    assert not any(event["event_type"] == "task_timed_out"
                   for event in store.list_events(task["id"]))


@pytest.mark.asyncio
async def test_total_task_budget_stops_read_and_records_timeout(tmp_path):
    stopped = asyncio.Event()

    async def slow_paper(session_id):
        assert session_id == "paper:mine"
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    harness, store = _harness(tmp_path, paper_handler=slow_paper)
    harness.config.update(agent_model_planning_enabled=False,
                          agent_task_timeout_seconds=0.05)
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "评估模拟盘风险")
    await asyncio.wait_for(harness._active[task["id"]], timeout=2)

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    events = detail["events"]
    assert stopped.is_set()
    assert detail["status"] == "failed"
    assert "总时限" in detail["error"]
    assert detail["proposal"] is None
    assert next(event["payload"]["budget"]["total_seconds"] for event in events
                if event["event_type"] == "task_created") == 0.05
    assert any(event["event_type"] == "step_completed" and
               event["payload"].get("reason") == "timeout" for event in events)
    assert any(event["event_type"] == "task_timed_out" for event in events)


@pytest.mark.asyncio
async def test_late_tool_result_cannot_create_paper_proposal(tmp_path):
    returned_after_cancel = asyncio.Event()

    async def slow_paper(session_id):
        assert session_id == "paper:mine"
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            returned_after_cancel.set()
            return _paper_status(as_of_date="2026-09-25")

    harness, store = _harness(tmp_path, paper_handler=slow_paper)
    harness.config.update(agent_model_planning_enabled=False,
                          agent_task_timeout_seconds=0.05)
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "推进模拟盘到 2026-09-28")
    await asyncio.wait_for(harness._active[task["id"]], timeout=2)

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert returned_after_cancel.is_set()
    assert detail["status"] == "failed"
    assert detail["evidence"] == []
    assert detail["proposal"] is None
    assert not any(event["event_type"] == "proposal_created" for event in detail["events"])


@pytest.mark.asyncio
async def test_late_model_answer_cannot_complete_task(tmp_path):
    async def paper(session_id):
        assert session_id == "paper:mine"
        return _paper_status(as_of_date="2026-09-25")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config.update(agent_model_planning_enabled=False,
                          agent_task_timeout_seconds=0.05)

    async def slow_synthesis(*_args, **_kwargs):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return "超时后返回的回答"

    harness._synthesize = slow_synthesis
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "这个模拟盘账户的权益如何？")
    await asyncio.wait_for(harness._active[task["id"]], timeout=2)

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert detail["status"] == "failed"
    assert len(detail["evidence"]) == 1
    assert not any(message["role"] == "assistant"
                   for message in store.list_messages(conversation["id"]))
    assert any(event["event_type"] == "task_timed_out" for event in detail["events"])


@pytest.mark.asyncio
async def test_total_task_budget_includes_queue_wait(tmp_path):
    async def paper(_session_id):
        raise AssertionError("排队超时的任务不应调用账本")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config.update(agent_model_planning_enabled=False,
                          agent_task_timeout_seconds=0.05)
    for _ in range(3):
        await harness._slots.acquire()
    try:
        conversation = store.create_conversation("测试", "paper:mine")
        task = harness.submit(conversation["id"], "评估模拟盘风险")
        await asyncio.wait_for(harness._active[task["id"]], timeout=2)
        assert store.get_task(task["id"])["status"] == "failed"
        assert any(event["event_type"] == "task_timed_out"
                   for event in store.list_events(task["id"]))
    finally:
        for _ in range(3):
            harness._slots.release()


@pytest.mark.asyncio
async def test_cancel_before_task_starts_records_terminal_status(tmp_path):
    async def paper(_session_id):
        raise AssertionError("取消的任务不应读取账本")

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "解释风险")
    running = harness._active[task["id"]]
    assert await harness.cancel(task["id"])
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)
    assert store.get_task(task["id"])["status"] == "cancelled"
    assert any(event["event_type"] == "task_cancelled"
               for event in store.list_events(task["id"]))


@pytest.mark.asyncio
async def test_same_skill_in_two_conversations_has_independent_cancel_and_final_progress(tmp_path):
    class Params(BaseModel):
        value: str = "same"

    started = asyncio.Event()
    finish = asyncio.Event()
    runs_started = 0

    class SlowAnalysis(BaseSkill):
        @property
        def metadata(self):
            return SkillMetadata(id="stock_analysis", name="Test analysis",
                                 description="test", version="1")

        @property
        def input_schema(self):
            return Params

        @property
        def output_schema(self):
            return Params

        async def execute(self, params, config):
            nonlocal runs_started
            runs_started += 1
            if runs_started == 2:
                started.set()
            yield SkillEvent(event_type="agent_status", data={"run_id": config["run_id"]})
            await finish.wait()
            yield SkillEvent(event_type="skill_progress", data={
                "stage_id": "done", "stage_label": "分析完成", "status": "completed",
            })
            yield SkillEvent(event_type="skill_complete", data={
                "status": "success", "value": params.value,
            })

        async def cancel(self):
            return None

    class Skills:
        def get(self, skill_id):
            return skill if skill_id == "stock_analysis" else None

    async def unused_paper(session_id):
        raise AssertionError(f"未绑定模拟盘，不应读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.run_manager = RunManager(db=store.db)
    skill = SlowAnalysis()
    harness.skills = Skills()

    async def synthesize(*_args, **_kwargs):
        return "分析已完成。"

    harness._synthesize = synthesize
    conversations = [store.create_conversation(f"测试 {index}", None) for index in range(2)]
    hint = {"skill_id": "stock_analysis", "params": {"value": "same"}}
    tasks = [harness.submit(conversation["id"], "进行深度研究", hint)
             for conversation in conversations]
    active = [harness._active[task["id"]] for task in tasks]
    await asyncio.wait_for(started.wait(), timeout=2)

    run_ids = [next(event["payload"]["run_id"] for event in store.list_events(task["id"])
                    if event["event_type"] == "skill_started") for task in tasks]
    assert run_ids[0] != run_ids[1]
    assert await harness.cancel(tasks[0]["id"])
    await active[0]
    assert harness.run_manager.get_run(run_ids[0]).status == RunStatus.CANCELLED
    assert store.get_task(tasks[0]["id"])["status"] == "cancelled"

    finish.set()
    await asyncio.wait_for(active[1], timeout=2)
    assert harness.run_manager.get_run(run_ids[1]).status == RunStatus.COMPLETED
    assert store.get_task(tasks[1]["id"])["status"] == "completed"
    progress = [event for event in store.list_events(tasks[1]["id"])
                if event["event_type"] == "skill_progress"]
    assert [event["payload"]["event_type"] for event in progress] == [
        "agent_status", "skill_progress",
    ]
    assert store.list_evidence(tasks[1]["id"])[0]["result"]["run_id"] == run_ids[1]


@pytest.mark.asyncio
async def test_agent_skill_timeout_cancels_run_and_records_recoverable_failure(tmp_path):
    class Params(BaseModel):
        value: str = "same"

    started = asyncio.Event()
    stopped = asyncio.Event()

    class StalledAnalysis(BaseSkill):
        @property
        def metadata(self):
            return SkillMetadata(id="stock_analysis", name="Stalled analysis",
                                 description="test", version="1")

        @property
        def input_schema(self):
            return Params

        @property
        def output_schema(self):
            return Params

        async def execute(self, _params, _config):
            started.set()
            try:
                await asyncio.Event().wait()
                yield SkillEvent(event_type="skill_complete", data={
                    "status": "success", "value": "never",
                })
            finally:
                stopped.set()

        async def cancel(self):
            raise RuntimeError("cooperative cancellation hook failed")

    class Skills:
        def get(self, skill_id):
            return skill if skill_id == "stock_analysis" else None

    async def unused_paper(session_id):
        raise AssertionError(f"未绑定模拟盘，不应读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config.update(agent_model_planning_enabled=False,
                          agent_skill_timeout_seconds=0.05)
    harness.run_manager = RunManager(db=store.db)
    skill = StalledAnalysis()
    harness.skills = Skills()
    conversation = store.create_conversation("超时研究", None)
    task = harness.submit(conversation["id"], "进行深度研究", {
        "skill_id": "stock_analysis", "params": {"value": "same"},
    })
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.wait_for(harness._active[task["id"]], timeout=2)

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    run_id = next(event["payload"]["run_id"] for event in detail["events"]
                  if event["event_type"] == "skill_started")
    assert stopped.is_set()
    assert harness.run_manager.get_run(run_id).status == RunStatus.CANCELLED
    assert store.db.get_run(run_id)["status"] == "cancelled"
    assert detail["status"] == "completed"
    assert "超过 0.05 秒" in detail["evidence"][0]["result"]["error"]
    assert "没有生成交易判断" in detail["result"]["content"]
    assert any(event["event_type"] == "skill_timed_out" and
               event["payload"]["run_id"] == run_id for event in detail["events"])
    assert any(event["event_type"] == "step_completed" and
               event["payload"]["status"] == "failed" for event in detail["events"])


@pytest.mark.asyncio
async def test_requested_skill_failure_cannot_be_replaced_by_paper_ledger_answer(
    tmp_path, monkeypatch,
):
    async def paper(session_id):
        return {"session_id": session_id, "as_of_date": "2026-09-25",
                "source": "StockManager ledger", "snapshot": {"equity": 100000}}

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.skills = SimpleNamespace(get=lambda name: object() if name == "stock_analysis" else None)

    async def failed_skill(*_args, **_kwargs):
        return {"error": "分析子任务超过时限", "run_id": "run:timeout",
                "source": "TradingAgents Skill: stock_analysis"}

    harness._run_skill = failed_skill
    monkeypatch.setattr("tradingagents.llm_clients.create_llm_client",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            AssertionError("必需的分析 Skill 失败后不得调用模型")))
    conversation = store.create_conversation("模拟盘研究", "paper:mine")
    task = harness.submit(conversation["id"], "分析当前模拟盘的策略", {
        "skill_id": "stock_analysis", "params": {},
    })
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert [item["tool_name"] for item in detail["evidence"]] == [
        "get_paper_session", "skill",
    ]
    assert detail["status"] == "completed"
    assert "分析子任务超过时限" in detail["result"]["content"]
    assert "没有生成交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_shutdown_marks_read_task_interrupted_for_explicit_retry(tmp_path):
    started = asyncio.Event()

    async def slow_paper(session_id):
        started.set()
        await asyncio.sleep(60)
        return {"session_id": session_id}

    harness, store = _harness(tmp_path, paper_handler=slow_paper)
    conversation = store.create_conversation("测试", "paper:shutdown")
    task = harness.submit(conversation["id"], "评估当前模拟盘")
    await asyncio.wait_for(started.wait(), timeout=2)
    await harness.close()
    assert store.get_task(task["id"])["status"] == "interrupted"
    assert store.list_events(task["id"])[-1]["event_type"] == "task_interrupted"


@pytest.mark.asyncio
async def test_write_skill_is_not_dispatched(tmp_path):
    async def unused_paper(session_id):
        raise AssertionError("不应调用模拟盘")

    chat = _Chat(ChatResponse(intent="skill_run", skill_id="portfolio_management", skill_params={}))
    harness, store = _harness(tmp_path, paper_handler=unused_paper, chat=chat)
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "请执行 portfolio_management")
    await harness._active[task["id"]]
    assert "不会直接执行" in store.get_task(task["id"])["result"]["content"]


@pytest.mark.asyncio
async def test_fallback_chat_cannot_import_unscoped_tool_result(tmp_path):
    async def unused_paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    class UnexpectedToolChat(_Chat):
        async def handle(self, *_args, **kwargs):
            assert kwargs["allow_tools"] is False
            return ChatResponse(intent="tool_answer", tool_name="get_paper_session",
                                tool_result={"session_id": "paper:other", "snapshot": {"equity": 1}})

    harness, store = _harness(tmp_path, paper_handler=unused_paper, chat=UnexpectedToolChat())
    harness.config["agent_model_planning_enabled"] = False
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "你好")
    await harness._active[task["id"]]

    assert store.list_evidence(task["id"]) == []
    assert "重新核对来源、账户与日期" in store.get_task(task["id"])["result"]["content"]


def test_reopen_marks_incomplete_task_interrupted(tmp_path):
    db = Database(tmp_path / "agent.db")
    store = AgentStore(db)
    conversation = store.create_conversation("测试", None)
    task = store.create_task(conversation["id"], "分析")
    reopened = AgentStore(Database(tmp_path / "agent.db"))
    assert reopened.get_task(task["id"])["status"] == "interrupted"


def test_intent_hint_only_selects_allowlisted_analysis_skill(tmp_path):
    async def unused_paper(session_id):
        return {"session_id": session_id}

    harness, _ = _harness(tmp_path, paper_handler=unused_paper)

    class SafeSkills:
        def get(self, name):
            return object() if name == "stock_analysis" else None

    harness.skills = SafeSkills()
    safe = harness._plan("分析茅台", None, {
        "skill_id": "stock_analysis", "params": {"ticker": "600519.SH"}})
    assert safe[0]["tool"] == "skill"
    assert safe[0]["args"] == {"ticker": "600519.SH"}
    blocked = harness._plan("清空账户", None, {
        "skill_id": "portfolio_management", "params": {"action": "clear"}})
    assert all(step["tool"] != "skill" for step in blocked)


@pytest.mark.asyncio
async def test_model_plan_cannot_change_bound_paper_account_or_run_write_skill(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id}

    harness, _ = _harness(tmp_path, paper_handler=paper)

    async def malicious_plan(*_args, **_kwargs):
        return [
            {"tool": "get_paper_session", "args": {"session_id": "paper:other"}},
            {"tool": "get_portfolio_summary"},
            {"tool": "skill", "skill_id": "portfolio_management", "args": {"action": "clear"}},
            {"tool": "advance_paper_day", "args": {"target_date": "2026-09-28"}},
            {"tool": "search_artifacts", "query": "策略报告"},
        ]

    harness._request_model_plan = malicious_plan
    plan, source = await harness._build_plan("解释模拟盘", "paper:mine", None)
    assert source == "model"
    assert [(step["tool"], step["args"]) for step in plan] == [
        ("get_paper_session", {"session_id": "paper:mine"}),
    ]


@pytest.mark.asyncio
async def test_model_cannot_switch_explicit_stock_symbol(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, _ = _harness(tmp_path, paper_handler=paper)

    async def proposed(*_args, **_kwargs):
        return [{"tool": "get_mcp_factor_snapshot", "args": {"ts_code": "000001.SZ"}}]

    harness._request_model_plan = proposed
    plan, source = await harness._build_plan("看 600519.sh 估值", None, None)
    assert source == "model"
    assert [(step["tool"], step["args"]) for step in plan] == [
        ("get_mcp_factor_snapshot", {"ts_code": "600519.SH"}),
    ]


@pytest.mark.asyncio
async def test_explicit_stock_question_reads_factor_evidence(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    calls = []

    async def factor(ts_code):
        calls.append(ts_code)
        return {"ts_code": ts_code, "as_of_date": "2026-09-25",
                "snapshot": {"valuation": 12.0}}

    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor, data_source="StockManager MCP",
    ))

    async def synthesize(*_args, **_kwargs):
        return "截至 2026-09-25 的因子快照已读取。"

    harness._synthesize = synthesize
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "看 600519.sh 估值")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == ["600519.SH"]
    assert detail["evidence"][0]["as_of_date"] == "2026-09-25"
    assert detail["evidence"][0]["source"] == "StockManager MCP"
    assert detail["result"]["citations"][0]["tool_name"] == "get_mcp_factor_snapshot"


@pytest.mark.asyncio
async def test_factor_failure_does_not_become_current_stock_advice(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False

    async def factor(ts_code):
        return {"error": f"{ts_code} 行情不可用"}

    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor,
    ))
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "看 600519.SH 估值")
    await harness._active[task["id"]]
    content = store.get_task(task["id"])["result"]["content"]
    assert "无法核对" in content
    assert "没有生成交易判断" in content


@pytest.mark.asyncio
async def test_paper_stock_question_requires_both_ledger_and_factor_snapshot(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25", "snapshot": {"equity": 100000}}

    async def factor(ts_code, trade_date=""):
        return {"error": f"{ts_code} 因子源不可用"}

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor,
    ))
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "评估模拟盘内 600519.SH 的风险")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert [item["tool_name"] for item in detail["evidence"]] == [
        "get_paper_session", "get_mcp_factor_snapshot",
    ]
    assert "没有生成交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_paper_factor_query_uses_verified_ledger_date(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25", "snapshot": {"equity": 100000}}

    calls = []

    async def factor(ts_code, trade_date=""):
        calls.append((ts_code, trade_date))
        return {"ts_code": ts_code, "as_of_date": trade_date,
                "source": "StockManager MCP", "snapshot": {"rows": []}}

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor,
    ))

    async def synthesize(*_args, **_kwargs):
        return "同一基准日的证据已核对。"

    harness._synthesize = synthesize
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "评估模拟盘内 600519.SH 的风险")
    await harness._active[task["id"]]

    assert calls == [("600519.SH", "2026-09-25")]
    started = [event for event in store.list_events(task["id"])
               if event["event_type"] == "step_started"]
    assert started[1]["payload"]["args"]["trade_date"] == "2026-09-25"


@pytest.mark.asyncio
async def test_paper_factor_date_conflict_abstains_before_model(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id, "as_of_date": "2026-09-25",
                "snapshot": {"equity": 100000}}

    calls = []

    async def factor(ts_code, trade_date=""):
        calls.append((ts_code, trade_date))
        assert trade_date == "2026-09-25"
        return {"ts_code": ts_code, "as_of_date": "2026-09-24",
                "source": "StockManager MCP", "snapshot": {"rows": []}}

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor, retry_policy="date_conflict_once",
    ))
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "评估模拟盘内 600519.SH 的风险")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == [("600519.SH", "2026-09-25")] * 2
    assert len([event for event in detail["events"]
                if event["event_type"] == "plan_revised"]) == 1
    assert "不能把不同日期的数据合并" in detail["result"]["content"]
    assert "基准日不一致" in detail["evidence"][1]["warnings"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_result", ["wrong_date", "tool_error"])
async def test_paper_factor_date_conflict_recovers_with_one_read_only_retry(
    tmp_path, monkeypatch, first_result,
):
    async def paper(session_id):
        return {"session_id": session_id, "as_of_date": "2026-09-25",
                "snapshot": {"equity": 100000}}

    calls = []

    async def factor(ts_code, trade_date=""):
        calls.append((ts_code, trade_date))
        if len(calls) == 1 and first_result == "tool_error":
            return {"ts_code": ts_code, "error": "因子快照基准日与请求交易日不一致"}
        return {"ts_code": ts_code, "as_of_date": "2026-09-24" if len(calls) == 1 else trade_date,
                "source": "StockManager MCP", "snapshot": {"rows": []}}

    captured = []

    class FakeLLM:
        async def ainvoke(self, messages):
            captured.extend(messages)
            return SimpleNamespace(content="复核后基准日一致。")

    monkeypatch.setattr("tradingagents.llm_clients.create_llm_client",
                        lambda **_kwargs: SimpleNamespace(get_llm=FakeLLM))
    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor, retry_policy="date_conflict_once",
    ))
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "评估模拟盘内 600519.SH 的风险")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == [("600519.SH", "2026-09-25")] * 2
    assert detail["status"] == "completed"
    assert len(detail["evidence"]) == 3  # Both attempts remain auditable.
    assert detail["evidence"][1]["result"]["error"]
    if first_result == "tool_error":
        assert detail["evidence"][1]["result"]["source_error"] == "因子快照基准日与请求交易日不一致"
    assert detail["evidence"][2]["as_of_date"] == "2026-09-25"
    assert len([event for event in detail["events"]
                if event["event_type"] == "plan_revised"]) == 1
    model_evidence = json.loads(captured[1].content.split("证据 JSON：\n", 1)[1])
    assert len(model_evidence) == 2
    factors = [item for item in model_evidence if item["data"].get("ts_code") == "600519.SH"]
    assert len(factors) == 1
    assert factors[0]["as_of_date"] == "2026-09-25"
    assert not factors[0]["data"].get("error")
    assert "2026-09-24" not in detail["result"]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("first_result", ["stale", "tool_error"])
async def test_current_trade_decision_rechecks_stale_factor_before_model(
    tmp_path, monkeypatch, recover, first_result,
):
    monkeypatch.setattr("tradingagents.core.trading_time.get_temporal_context",
                        lambda *_args, **_kwargs: SimpleNamespace(market_asof_date="2026-09-25"))
    model_inputs = []

    class FakeLLM:
        async def ainvoke(self, messages):
            model_inputs.append(messages)
            return SimpleNamespace(content="按已核对日期评估。")

    monkeypatch.setattr("tradingagents.llm_clients.create_llm_client",
                        lambda **_kwargs: SimpleNamespace(get_llm=FakeLLM))
    calls = []

    async def factor(ts_code):
        calls.append(ts_code)
        if first_result == "tool_error" and len(calls) == 1:
            return {"ts_code": ts_code, "source": "StockManager MCP",
                    "error": "因子快照基准日与请求交易日不一致"}
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-25" if recover and len(calls) == 2 else "2026-09-24",
                "snapshot": {"rows": []}}

    async def unused_paper(session_id):
        raise AssertionError(f"未绑定模拟盘，不应读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={}, handler=factor,
        retry_policy="date_conflict_once",
    ))
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "现在要不要买入 600519.SH？")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == ["600519.SH"] * 2
    assert [item["as_of_date"] for item in detail["evidence"]] == [
        None if first_result == "tool_error" else "2026-09-24",
        "2026-09-25" if recover else "2026-09-24",
    ]
    assert detail["evidence"][0]["result"]["error"]
    if first_result == "tool_error":
        assert detail["evidence"][0]["result"]["source_error"] == "因子快照基准日与请求交易日不一致"
    assert len([event for event in detail["events"]
                if event["event_type"] == "plan_revised"]) == 1
    assert any(event["event_type"] == "market_time_bound" and
               event["payload"]["market_asof_date"] == "2026-09-25"
               for event in detail["events"])
    if recover:
        assert len(model_inputs) == 1
        model_evidence = json.loads(model_inputs[0][1].content.split("证据 JSON：\n", 1)[1])
        assert len(model_evidence) == 1
        assert model_evidence[0]["as_of_date"] == "2026-09-25"
        assert detail["result"]["content"].startswith("按已核对日期评估。")
    else:
        assert not model_inputs
        assert "没有生成交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_current_trade_decision_rechecks_stale_announcement_and_abstains(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("tradingagents.core.trading_time.get_temporal_context",
                        lambda *_args, **_kwargs: SimpleNamespace(market_asof_date="2026-09-25"))
    monkeypatch.setattr("tradingagents.llm_clients.create_llm_client",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            AssertionError("过期公告不得交给模型生成交易判断")))
    calls = []

    async def announcements(ts_code, end_date=""):
        calls.append(ts_code)
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-24", "rows": [], "count": 0}

    async def unused_paper(session_id):
        raise AssertionError(f"未绑定模拟盘，不应读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_risk_announcements", description="risk", parameters={},
        handler=announcements, retry_policy="date_conflict_once",
    ))
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "600519.SH 公告后现在还能卖出吗？")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == ["600519.SH"] * 2
    assert all(item["result"].get("error") for item in detail["evidence"])
    assert "没有生成交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_trade_execution_question_needs_factor_and_does_not_claim_sellability(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("tradingagents.core.trading_time.get_temporal_context",
                        lambda *_args, **_kwargs: SimpleNamespace(market_asof_date="2026-09-25"))
    monkeypatch.setattr("tradingagents.llm_clients.create_llm_client",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            AssertionError("缺少成交条件时不得交给模型判断能否卖出")))

    async def factor(ts_code):
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-25", "snapshot": {"rows": [{"ts_code": ts_code}]}}

    async def announcements(ts_code, end_date=""):
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-25", "rows": [], "count": 0}

    async def unused_paper(session_id):
        raise AssertionError(f"未绑定模拟盘，不应读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={}, handler=factor,
    ))
    harness.tools.register(LightweightTool(
        name="get_mcp_risk_announcements", description="risk", parameters={},
        handler=announcements,
    ))
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "600519.SH 公告后现在还能卖出吗？")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert [item["tool_name"] for item in detail["evidence"]] == [
        "get_mcp_factor_snapshot", "get_mcp_risk_announcements",
    ]
    assert "无法判断这笔交易能否成交" in detail["result"]["content"]
    assert "没有生成可执行的交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_structured_answer_keeps_only_current_task_evidence_refs(tmp_path, monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages):
            supplied = json.loads(messages[1].content.split("证据 JSON：\n", 1)[1])
            return SimpleNamespace(content=json.dumps({
                "summary": "估值数据已读取，仍需核对缺失维度。",
                "verdict": "conditional",
                "reasons": ["因子快照的日期已标明。"],
                "risks": ["缺失维度不能解释为中性。"],
                "assumptions": ["仅讨论当前标的。"],
                "evidence_refs": [supplied[0]["id"], "other-task-evidence"],
                "next_actions": ["核对完整因子定义。"],
            }, ensure_ascii=False))

    monkeypatch.setattr("tradingagents.llm_clients.create_llm_client",
                        lambda **_kwargs: SimpleNamespace(get_llm=FakeLLM))

    async def factor(ts_code):
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-25", "snapshot": {"rows": [{"ts_code": ts_code}]}}

    async def unused_paper(session_id):
        raise AssertionError(f"未绑定模拟盘，不应读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={}, handler=factor,
    ))
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "分析 600519.SH 的估值")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    answer = detail["result"]["answer"]
    assert answer["verdict"] == "conditional"
    assert answer["evidence_refs"] == [detail["evidence"][0]["id"]]
    assert "other-task-evidence" not in detail["result"]["content"]
    assert "判断：有条件判断" in detail["result"]["content"]
    assert "风险：" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_current_trade_decision_without_market_date_stops_before_tools(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("tradingagents.core.trading_time.get_temporal_context",
                        lambda *_args, **_kwargs: SimpleNamespace(market_asof_date=""))

    async def unused_paper(session_id):
        raise AssertionError(f"未绑定模拟盘，不应读取 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=unused_paper)
    harness.config["agent_model_planning_enabled"] = False
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "现在要不要买入 600519.SH？")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert detail["status"] == "completed"
    assert detail["evidence"] == []
    assert "市场基准日无法核定" in detail["result"]["content"]
    assert "没有生成交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_two_explicit_stocks_each_get_a_bound_factor_snapshot(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    calls = []

    async def factor(ts_code):
        calls.append(ts_code)
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-25", "snapshot": {"rows": [{"ts_code": ts_code}]}}

    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor,
    ))

    async def synthesize(*_args, **_kwargs):
        return "两个标的证据已核对。"

    harness._synthesize = synthesize
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "比较 600519.SH 和 000001.SZ")
    await harness._active[task["id"]]
    assert calls == ["600519.SH", "000001.SZ"]
    assert store.get_task(task["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_more_than_two_explicit_stocks_asks_to_narrow_scope(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"],
                          "比较 600519.SH、000001.SZ 和 688981.SH")
    await harness._active[task["id"]]
    result = store.get_task(task["id"])
    assert result["status"] == "needs_input"
    assert "最多核对两个" in result["result"]["content"]
    assert store.list_evidence(task["id"]) == []


@pytest.mark.asyncio
async def test_announcement_question_reads_dates_without_factor_snapshot(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    calls = []

    async def announcements(ts_code, end_date=""):
        calls.append((ts_code, end_date))
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-25", "start_date": "2026-06-27",
                "end_date": "2026-09-25", "rows": [], "count": 0,
                "warnings": ["零命中不代表没有风险公告"]}

    harness.tools.register(LightweightTool(
        name="get_mcp_risk_announcements", description="risk", parameters={},
        handler=announcements,
    ))

    async def synthesize(*_args, **_kwargs):
        return "未返回风险关键词命中；不能据此判断没有风险公告。"

    harness._synthesize = synthesize
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "查看 600519.SH 的风险公告")
    await harness._active[task["id"]]
    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == [("600519.SH", "")]
    assert [item["tool_name"] for item in detail["evidence"]] == ["get_mcp_risk_announcements"]
    assert detail["result"]["content"].startswith("未返回风险关键词")


@pytest.mark.asyncio
async def test_model_cannot_switch_announcement_symbol_or_cutoff(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, _ = _harness(tmp_path, paper_handler=paper)

    async def proposed(*_args, **_kwargs):
        return [{"tool": "get_mcp_risk_announcements",
                 "args": {"ts_code": "000001.SZ", "end_date": "2025-01-01"}}]

    harness._request_model_plan = proposed
    plan, source = await harness._build_plan("查看 600519.SH 的立案公告", None, None)
    assert source == "model"
    assert [(step["tool"], step["args"]) for step in plan] == [
        ("get_mcp_risk_announcements", {"ts_code": "600519.SH"}),
    ]


def test_announcement_plan_ignores_unrequested_archive_search(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, _ = _harness(tmp_path, paper_handler=paper)
    plan = harness._validate_model_steps(
        [{"tool": "search_artifacts", "query": "旧公告"},
         {"tool": "get_portfolio_summary"}],
        "查看 600519.SH 的公告", None,
    )
    assert [step["tool"] for step in plan] == ["get_mcp_risk_announcements"]


@pytest.mark.asyncio
async def test_paper_announcement_cutoff_is_bound_to_ledger_and_conflict_abstains(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25", "snapshot": {"equity": 100000}}

    calls = []

    async def announcements(ts_code, end_date=""):
        calls.append((ts_code, end_date))
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-24", "rows": [{"ann_date": "2026-09-20"}], "count": 1}

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_risk_announcements", description="risk", parameters={},
        handler=announcements, retry_policy="date_conflict_once",
    ))
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "模拟盘 600519.SH 的问询公告如何？")
    await harness._active[task["id"]]
    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == [("600519.SH", "2026-09-25")] * 2
    assert len([event for event in detail["events"]
                if event["event_type"] == "plan_revised"]) == 1
    assert detail["evidence"][1]["result"]["error"] == "风险公告查询截止日与模拟盘账本不一致"
    assert "没有生成交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_paper_announcement_skips_current_scan_without_ledger_date(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id, "source": "StockManager ledger",
                "snapshot": {"equity": 100000}}

    async def announcements(ts_code, end_date=""):
        raise AssertionError("账本缺少日期时不得查询当前公告")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    harness.tools.register(LightweightTool(
        name="get_mcp_risk_announcements", description="risk", parameters={},
        handler=announcements,
    ))
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "模拟盘 600519.SH 的问询公告如何？")
    await harness._active[task["id"]]
    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert "账本缺少有效基准日" in detail["evidence"][1]["result"]["error"]
    assert "没有生成交易判断" in detail["result"]["content"]


@pytest.mark.asyncio
async def test_paper_factor_and_announcement_scope_requires_one_stock(tmp_path):
    async def paper(session_id):
        raise AssertionError("超出取证步数前不得读取账本")

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "比较 600519.SH 与 000001.SZ 的估值和公告")
    await harness._active[task["id"]]
    result = store.get_task(task["id"])
    assert result["status"] == "needs_input"
    assert "缩小到一只股票" in result["result"]["content"]
    assert store.list_evidence(task["id"]) == []


@pytest.mark.asyncio
async def test_followups_reuse_one_previous_conversation_symbol(tmp_path):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.config["agent_model_planning_enabled"] = False
    calls = []
    factor_calls = []

    async def factor(ts_code):
        factor_calls.append(ts_code)
        return {"ts_code": ts_code, "as_of_date": "2026-09-25", "source": "MCP",
                "snapshot": {"rows": [{"ts_code": ts_code}]}}

    async def announcements(ts_code, end_date=""):
        calls.append(ts_code)
        return {"ts_code": ts_code, "as_of_date": "2026-09-25", "source": "MCP",
                "start_date": "2026-06-27", "end_date": "2026-09-25",
                "rows": [], "count": 0}

    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={}, handler=factor,
    ))
    harness.tools.register(LightweightTool(
        name="get_mcp_risk_announcements", description="risk", parameters={},
        handler=announcements,
    ))

    async def synthesize(*_args, **_kwargs):
        return "已核对工具证据"

    harness._synthesize = synthesize
    conversation = store.create_conversation("测试", None)
    first = harness.submit(conversation["id"], "看 600519.SH 估值")
    await harness._active[first["id"]]
    followup = harness.submit(conversation["id"], "那它的公告呢？")
    await harness._active[followup["id"]]

    detail = store.get_task(followup["id"])
    events = store.list_events(followup["id"])
    assert detail["status"] == "completed"
    assert detail["goal"] == "那它的公告呢？"
    assert calls == ["600519.SH"]
    assert [item["tool_name"] for item in store.list_evidence(followup["id"])] == [
        "get_mcp_risk_announcements",
    ]
    assert next(event["payload"]["ts_code"] for event in events
                if event["event_type"] == "scope_resolved") == "600519.SH"

    third = harness.submit(conversation["id"], "那它的估值呢？")
    await harness._active[third["id"]]
    assert store.get_task(third["id"])["status"] == "completed"
    assert factor_calls == ["600519.SH", "600519.SH"]
    assert [item["tool_name"] for item in store.list_evidence(third["id"])] == [
        "get_mcp_factor_snapshot",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_goal, expected", [
    (None, "请提供"),
    ("比较 600519.SH 和 000001.SZ", "多只股票"),
    ("你好", "没有明确"),
])
async def test_stock_followup_without_unique_prior_symbol_requests_code(
    tmp_path, previous_goal, expected,
):
    async def paper(session_id):
        raise AssertionError(f"不应读取模拟盘 {session_id}")

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("测试", None)
    if previous_goal is not None:
        previous = store.create_task(conversation["id"], previous_goal)
        store.set_status(previous["id"], "completed", result={"content": "旧任务"})
    task = harness.submit(conversation["id"], "那它的公告呢？")
    await harness._active[task["id"]]
    detail = store.get_task(task["id"])
    assert detail["status"] == "needs_input"
    assert expected in detail["result"]["content"]
    assert store.list_evidence(task["id"]) == []


@pytest.mark.asyncio
async def test_failed_required_portfolio_source_does_not_search_unrequested_archives(tmp_path):
    async def paper(session_id):
        raise AssertionError("不应读取模拟盘")

    harness, store = _harness(tmp_path, paper_handler=paper)
    harness.tools.register(LightweightTool(
        name="get_portfolio_summary", description="portfolio", parameters={},
        handler=lambda: asyncio.sleep(0, result={"error": "持仓库暂时不可用"}),
    ))
    harness.tools.register(LightweightTool(
        name="search_artifacts", description="artifacts", parameters={},
        handler=lambda **_kwargs: asyncio.sleep(0, result={"source": "历史产物", "total": 1}),
    ))
    requests = []

    async def plans(*_args, **kwargs):
        requests.append(kwargs.get("evidence"))
        return ([{"tool": "get_portfolio_summary"}] if len(requests) == 1 else
                [{"tool": "search_artifacts", "query": "风险报告"}])

    harness._request_model_plan = plans
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "评估当前持仓风险")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert detail["status"] == "completed"
    assert [item["tool_name"] for item in detail["evidence"]] == ["get_portfolio_summary"]
    assert not any(event["event_type"] == "plan_revised" for event in detail["events"])
    assert "没有生成交易判断" in detail["result"]["content"]
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_model_plan_rejects_unknown_tools_and_falls_back(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id}

    harness, _ = _harness(tmp_path, paper_handler=paper)

    async def invalid_plan(*_args, **_kwargs):
        return [{"tool": "execute_trade", "args": {"quantity": 100}}]

    harness._request_model_plan = invalid_plan
    plan, source = await harness._build_plan("你好", None, None)
    assert source == "rules"
    assert [step["tool"] for step in plan] == ["chat_agent"]


@pytest.mark.asyncio
async def test_model_plan_allows_one_registered_analysis_skill(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id}

    harness, _ = _harness(tmp_path, paper_handler=paper)

    class SafeSkills:
        def get(self, name):
            return object() if name in {"stock_analysis", "market_scanner"} else None

    harness.skills = SafeSkills()

    async def proposed(*_args, **_kwargs):
        return [
            {"tool": "skill", "skill_id": "stock_analysis", "args": {"ticker": "600519.SH"}},
            {"tool": "skill", "skill_id": "market_scanner", "args": {}},
        ]

    harness._request_model_plan = proposed
    plan, source = await harness._build_plan("分析 600519.SH", None, None)
    assert source == "model"
    assert [(step["tool"], step.get("skill_id")) for step in plan] == [
        ("get_mcp_factor_snapshot", None),
        ("skill", "stock_analysis"),
    ]
    assert plan[0]["args"] == {"ts_code": "600519.SH"}
    assert plan[1]["args"] == {"ticker": "600519.SH"}


async def _proposed_advance(tmp_path):
    async def paper(session_id):
        ledger = {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25",
                "session": {"session_id": session_id, "strategy_hash": "strategy:v1",
                            "config_hash": "config:v1"},
                "snapshot": {"as_of_date": "2026-09-25", "equity": 100000,
                             "cash": 100000, "positions": {}}}
        ledger["state_fingerprint"] = _paper_state_fingerprint(ledger)
        return ledger

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("推进测试", "paper:advance")
    task = harness.submit(conversation["id"], "推进模拟盘到 2026-09-28")
    await harness._active[task["id"]]
    proposal = store.proposal_for_task(task["id"])
    assert store.get_task(task["id"])["status"] == "awaiting_approval"
    assert proposal["args"] == {"target_date": "2026-09-28"}
    return harness, store, task, proposal


def _paper_status(as_of_date="2026-09-25", *, equity=100000, cash=100000,
                  positions=None, strategy_hash="strategy:v1"):
    ledger = {
        "session": {"session_id": "paper:advance", "last_date": as_of_date,
                    "strategy_hash": strategy_hash, "config_hash": "config:v1"},
        "snapshot": {"as_of_date": as_of_date, "equity": equity, "cash": cash,
                     "positions": positions if positions is not None else {}},
    }
    ledger["state_fingerprint"] = _paper_state_fingerprint(ledger)
    return {"data": ledger}


def test_paper_fingerprint_ignores_display_enrichment_but_detects_account_change():
    ledger = _paper_status(positions={"600519.SH": {"shares": 100, "avg_cost": 80}})["data"]
    baseline = _paper_state_fingerprint(ledger)
    enriched = json.loads(json.dumps(ledger))
    enriched["snapshot"]["positions"]["600519.SH"].update({
        "name": "贵州茅台", "day_pnl": 200, "day_pnl_pct": 0.01, "prev_close": 100,
    })
    assert baseline == _paper_state_fingerprint(enriched)
    enriched["snapshot"]["positions"]["600519.SH"]["shares"] = 200
    assert baseline != _paper_state_fingerprint(enriched)


@pytest.mark.asyncio
async def test_incomplete_paper_ledger_cannot_create_advance_proposal(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id, "as_of_date": "2026-09-25",
                "snapshot": {"equity": 100000}}

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("缺失资金", "paper:advance")
    task = harness.submit(conversation["id"], "推进模拟盘到 2026-09-28")
    await harness._active[task["id"]]
    assert store.proposal_for_task(task["id"]) is None
    assert "无法安全准备推进提案" in store.get_task(task["id"])["result"]["content"]


@pytest.mark.asyncio
async def test_composite_child_cannot_create_direct_advance_proposal(tmp_path):
    async def paper(session_id):
        return {"session_id": session_id, "as_of_date": "2026-09-25",
                "session": {"session_id": session_id,
                            "params": {"composite_child": True,
                                       "parent_composite_id": "paper:group"}},
                "snapshot": {"as_of_date": "2026-09-25", "equity": 100000,
                             "cash": 100000, "positions": {}}}

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("子策略", "paper:sleeve")
    task = harness.submit(conversation["id"], "推进模拟盘到 2026-09-28")
    await harness._active[task["id"]]
    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert detail["status"] == "completed"
    assert detail["result"]["read_only"] is True
    assert detail["proposal"] is None
    assert "只能随所属组合推进" in detail["result"]["content"]
    assert any(event["event_type"] == "action_blocked" and
               event["payload"]["reason"] == "composite_child"
               for event in detail["events"])


@pytest.mark.asyncio
async def test_existing_child_proposal_is_blocked_before_post(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    ledger = _paper_status()["data"]
    ledger["session"]["params"] = {"composite_child": True,
                                   "parent_composite_id": "paper:group"}
    ledger["state_fingerprint"] = _paper_state_fingerprint(ledger)

    async def paper_request(_config, method, _path, _payload=None):
        assert method == "GET", "子策略旧提案不得提交推进请求"
        return {"data": ledger}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert store.get_proposal(proposal["id"])["status"] == "stale"
    assert store.get_task(task["id"])["result"]["read_only"] is True
    assert any(event["event_type"] == "action_blocked" and
               event["payload"]["reason"] == "composite_child"
               for event in store.list_events(task["id"]))


@pytest.mark.asyncio
async def test_unresolved_paper_advance_blocks_other_conversations_for_same_account(tmp_path):
    harness, store, _task, first = await _proposed_advance(tmp_path)
    assert store.claim_proposal(first["id"])
    store.set_proposal_status(first["id"], "unknown", {"job_id": "job:uncertain"})

    other = store.create_conversation("另一个对话", "paper:advance")
    blocked_task = harness.submit(other["id"], "推进模拟盘到 2026-09-29")
    await harness._active[blocked_task["id"]]
    blocked = store.conversation_detail(other["id"])["tasks"][0]
    assert blocked["proposal"] is None
    assert "先核对前一次执行结果" in blocked["result"]["content"]
    assert any(event["event_type"] == "action_blocked" for event in blocked["events"])

    pending_task = store.create_task(other["id"], "推进模拟盘到 2026-09-30")
    store.set_status(pending_task["id"], "awaiting_approval")
    second = store.create_proposal(pending_task["id"], "paper:advance",
                                   "2026-09-30", {"as_of_date": "2026-09-25"})
    assert not store.claim_proposal(second["id"])
    with pytest.raises(ValueError, match="先核对前一次推进"):
        harness.approve(second["id"])
    assert store.get_proposal(second["id"])["status"] == "pending"

    store.set_proposal_status(first["id"], "completed")
    assert store.claim_proposal(second["id"])


@pytest.mark.asyncio
async def test_manual_review_closes_uncertain_proposal_only_after_ledger_check(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "unknown", {"job_id": "job:lost"})
    store.set_status(task["id"], "needs_review")
    ledger = _paper_status("2026-09-26")["data"]
    ledger["advance_operation"] = {"job_id": "job:lost", "target_date": "2026-09-28",
                                   "state": "needs_review"}

    async def paper_request(config, method, path, payload=None):
        assert method == "GET" and path == "/api/v2/paper/paper:advance/status"
        return {"data": ledger}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    with pytest.raises(ValueError, match="仍未核对完成"):
        await harness.close_review(proposal["id"], ledger["state_fingerprint"])
    ledger["advance_operation"]["state"] = "reviewed"
    with pytest.raises(ValueError, match="账本在确认期间已变化"):
        await harness.close_review(proposal["id"], "0" * 64)
    result = await harness.close_review(proposal["id"], ledger["state_fingerprint"])
    assert result["status"] == "reviewed"
    assert result["result"]["reviewed_job_id"] == "job:lost"
    assert store.get_task(task["id"])["status"] == "completed"
    assert store.unresolved_paper_action("paper:advance") is None
    assert any(event["event_type"] == "action_reviewed" for event in store.list_events(task["id"]))
    with pytest.raises(ValueError, match="只有待核对"):
        await harness.close_review(proposal["id"], ledger["state_fingerprint"])


@pytest.mark.asyncio
async def test_composite_review_waits_for_child_operation_review(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "unknown", {"job_id": "job:group"})
    store.set_status(task["id"], "needs_review")
    parent = _paper_status("2026-09-26")["data"]
    parent["kind"] = "composite"
    parent["session"]["params"] = {"child_session_ids": ["paper:child"]}
    parent["advance_operation"] = {"job_id": "job:group", "target_date": "2026-09-28",
                                   "state": "reviewed"}
    parent["state_fingerprint"] = _paper_state_fingerprint(parent)
    child = {"session": {"session_id": "paper:child", "last_date": "2026-09-26"},
             "snapshot": {"as_of_date": "2026-09-26", "equity": 100_000},
             "advance_operation": {"job_id": "job:group", "state": "needs_review"}}

    async def paper_request(config, method, path, payload=None):
        assert method == "GET"
        return {"data": child if path.endswith("paper:child/status") else parent}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    with pytest.raises(ValueError, match="子策略推进作业仍待核对"):
        await harness.close_review(proposal["id"], parent["state_fingerprint"])
    child["advance_operation"]["state"] = "reviewed"
    assert (await harness.close_review(proposal["id"], parent["state_fingerprint"]))["status"] == "reviewed"


@pytest.mark.asyncio
async def test_advance_requires_one_time_approval_and_reconciles_ledger(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST":
            return {"job_id": "job:one"}
        if path.endswith("/status"):
            date_value = "2026-09-28" if any(call[0] == "POST" for call in calls) else "2026-09-25"
            return _paper_status(date_value, equity=101000 if date_value == "2026-09-28" else 100000)
        return {"state": "success", "result": {"data": {
            "session_id": "paper:advance", "last_date": "2026-09-28", "advanced_days": 1}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    assert harness.approve(proposal["id"])["status"] == "executing"
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert sum(method == "POST" for method, _, _ in calls) == 1
    assert store.get_proposal(proposal["id"])["status"] == "completed"
    assert store.get_task(task["id"])["status"] == "completed"
    assert store.get_task(task["id"])["result"]["action"]["as_of_date"] == "2026-09-28"


@pytest.mark.asyncio
async def test_advance_refuses_stale_account_before_write(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    calls = []

    async def changed(config, method, path, payload=None):
        calls.append(method)
        return {"data": {"snapshot": {"as_of_date": "2026-09-26"}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", changed)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert calls == ["GET"]
    assert store.get_proposal(proposal["id"])["status"] == "stale"


@pytest.mark.asyncio
async def test_advance_refuses_same_day_position_change_before_write(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    calls = []

    async def changed(config, method, path, payload=None):
        calls.append(method)
        return _paper_status(positions={"600519.SH": {"shares": 100, "avg_cost": 80}})

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", changed)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert calls == ["GET"]
    assert store.get_proposal(proposal["id"])["status"] == "stale"
    assert store.get_proposal(proposal["id"])["result"]["reason"] == "account_state_changed"
    assert "持仓或策略配置发生变化" in store.get_task(task["id"])["result"]["content"]


@pytest.mark.asyncio
async def test_old_proposal_without_account_fingerprint_cannot_write(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    with store.db._conn() as conn:
        conn.execute("UPDATE agent_proposals SET baseline_json = ? WHERE id = ?",
                     (json.dumps({"as_of_date": "2026-09-25", "equity": 100000}), proposal["id"]))
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append(method)
        return _paper_status()

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert calls == ["GET"]
    assert store.get_proposal(proposal["id"])["result"]["reason"] == "missing_baseline"
    assert "未执行" in store.get_task(task["id"])["result"]["content"]


@pytest.mark.asyncio
async def test_uncertain_post_never_retries(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    posts = 0

    async def uncertain(config, method, path, payload=None):
        nonlocal posts
        if method == "POST":
            posts += 1
            raise PaperServiceError("连接中断")
        return _paper_status()

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", uncertain)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    harness.approve(proposal["id"])
    assert posts == 1
    assert store.get_proposal(proposal["id"])["status"] == "unknown"
    assert store.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_reject_does_not_write_paper_account(tmp_path):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    result = harness.reject(proposal["id"])
    assert result["status"] == "rejected"
    assert store.get_task(task["id"])["status"] == "completed"
    assert "账本未发生变更" in store.get_task(task["id"])["result"]["content"]


@pytest.mark.asyncio
async def test_restart_never_replays_submitted_paper_action(tmp_path):
    _, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "submitted", {"job_id": "job:pending"})
    store.set_status(task["id"], "executing_action")
    reopened = AgentStore(Database(tmp_path / "agent.db"))
    assert reopened.get_task(task["id"])["status"] == "needs_review"
    assert reopened.get_proposal(proposal["id"])["status"] == "unknown"
    assert reopened.get_proposal(proposal["id"])["result"]["job_id"] == "job:pending"


@pytest.mark.asyncio
async def test_reconcile_submitted_job_after_restart_without_reposting(tmp_path, monkeypatch):
    _, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "submitted", {"job_id": "job:done"})
    store.set_status(task["id"], "executing_action")
    reopened = AgentStore(Database(tmp_path / "agent.db"))
    harness = TradingAgentHarness(reopened, None, None, None, None, {})
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append((method, path))
        if path == "/api/jobs/job:done":
            return {"state": "success", "result": {"data": {
                "session_id": "paper:advance", "last_date": "2026-09-28", "advanced_days": 1}}}
        return _paper_status("2026-09-28", equity=102000, cash=102000)

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "completed"
    assert reopened.get_task(task["id"])["status"] == "completed"
    assert calls == [("GET", "/api/jobs/job:done"),
                     ("GET", "/api/v2/paper/paper:advance/status")]
    assert (await harness.reconcile(proposal["id"]))["status"] == "completed"
    assert calls == [("GET", "/api/jobs/job:done"),
                     ("GET", "/api/v2/paper/paper:advance/status")]
    assert len([m for m in reopened.list_messages(task["conversation_id"]) if m["role"] == "assistant"]) == 2


@pytest.mark.asyncio
async def test_reconcile_successful_job_without_new_trading_day(tmp_path, monkeypatch):
    _, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "submitted", {"job_id": "job:no-day"})
    store.set_status(task["id"], "executing_action")
    reopened = AgentStore(Database(tmp_path / "agent.db"))
    harness = TradingAgentHarness(reopened, None, None, None, None, {})
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append((method, path))
        if path == "/api/jobs/job:no-day":
            return {"state": "success", "result": {"data": {
                "session_id": "paper:advance", "last_date": "2026-09-25", "advanced_days": 0}}}
        return _paper_status()

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "no_change"
    assert reopened.get_task(task["id"])["status"] == "completed"
    assert "账本未推进" in reopened.get_task(task["id"])["result"]["content"]
    assert (await harness.reconcile(proposal["id"]))["status"] == "no_change"
    assert calls == [("GET", "/api/jobs/job:no-day"),
                     ("GET", "/api/v2/paper/paper:advance/status")]


@pytest.mark.asyncio
async def test_success_receipt_cannot_hide_same_day_account_change(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "unknown", {"job_id": "job:zero-days"})
    store.set_status(task["id"], "needs_review")

    async def paper_request(config, method, path, payload=None):
        if path.startswith("/api/jobs/"):
            return {"state": "success", "result": {"data": {
                "session_id": "paper:advance", "last_date": "2026-09-25", "advanced_days": 0,
            }}}
        return _paper_status(cash=99_000)

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "unknown"
    assert store.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_success_receipt_requires_complete_verified_ledger(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "unknown", {"job_id": "job:incomplete"})
    store.set_status(task["id"], "needs_review")

    async def paper_request(config, method, path, payload=None):
        if path.startswith("/api/jobs/"):
            return {"state": "success", "result": {"data": {
                "session_id": "paper:advance", "last_date": "2026-09-28", "advanced_days": 1,
            }}}
        return {"data": {"session": {"session_id": "paper:advance", "last_date": "2026-09-28"},
                         "snapshot": {"as_of_date": "2026-09-28", "equity": 101_000}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "unknown"
    assert store.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_reconcile_missing_job_keeps_unknown_and_reports_ledger(tmp_path, monkeypatch):
    _, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "submitted", {"job_id": "job:lost"})
    store.set_status(task["id"], "executing_action")
    reopened = AgentStore(Database(tmp_path / "agent.db"))
    harness = TradingAgentHarness(reopened, None, None, None, None, {})

    async def paper_request(config, method, path, payload=None):
        assert method == "GET"
        if path.startswith("/api/jobs/"):
            raise PaperServiceError("Job not found", 404)
        return {"data": {"snapshot": {"as_of_date": "2026-09-28"}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "unknown"
    assert result["result"]["job_id"] == "job:lost"
    assert result["result"]["observed_date"] == "2026-09-28"
    assert reopened.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_lost_post_response_recovers_durable_receipt_without_reposting(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "unknown")
    store.set_status(task["id"], "needs_review")
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append((method, path))
        assert method == "GET"
        if path.endswith(f"/advance_requests/{proposal['id']}"):
            return {"data": {
                "client_request_id": proposal["id"], "session_id": "paper:advance",
                "job_id": "job:recovered", "target_date": "2026-09-28",
                "state": "completed", "result": {
                    "session_id": "paper:advance", "last_date": "2026-09-28",
                    "advanced_days": 1,
                },
            }}
        return _paper_status("2026-09-28", equity=101_000, cash=101_000)

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "completed"
    assert result["result"]["job_id"] == "job:recovered"
    assert store.get_task(task["id"])["status"] == "completed"
    assert calls == [
        ("GET", f"/api/v2/paper/paper:advance/advance_requests/{proposal['id']}"),
        ("GET", "/api/v2/paper/paper:advance/status"),
    ]


@pytest.mark.asyncio
async def test_success_receipt_must_match_account_ledger(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            return {"job_id": "job:other"}
        if path.startswith("/api/jobs/"):
            return {"state": "success", "result": {"data": {
                "session_id": "paper:someone-else", "last_date": "2026-09-28"}}}
        as_of = "2026-09-28" if store.get_proposal(proposal["id"])["status"] == "submitted" else "2026-09-25"
        return _paper_status(as_of)

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert store.get_proposal(proposal["id"])["status"] == "unknown"
    assert store.get_proposal(proposal["id"])["result"]["job_id"] == "job:other"
    assert store.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_poll_failure_keeps_job_id_for_later_reconciliation(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            return {"job_id": "job:recover"}
        if path.startswith("/api/jobs/"):
            raise PaperServiceError("服务重启")
        return _paper_status()

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    result = store.get_proposal(proposal["id"])
    assert result["status"] == "unknown"
    assert result["result"]["job_id"] == "job:recover"


@pytest.mark.asyncio
async def test_failed_job_with_changed_ledger_needs_review(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            return {"job_id": "job:partial"}
        if path.startswith("/api/jobs/"):
            return {"state": "error", "message": "计算中断"}
        as_of = "2026-09-26" if store.get_proposal(proposal["id"])["status"] != "executing" else "2026-09-25"
        return _paper_status(as_of)

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    result = store.get_proposal(proposal["id"])
    assert result["status"] == "unknown"
    assert result["result"]["observed_date"] == "2026-09-26"
    assert store.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_failed_job_same_day_drift_and_server_review_lock_stay_uncertain(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    assert store.claim_proposal(proposal["id"])
    store.set_proposal_status(proposal["id"], "unknown", {"job_id": "job:same-day"})
    store.set_status(task["id"], "needs_review")
    operation = {"job_id": "job:same-day", "state": "needs_review"}
    cash = 99_000

    async def paper_request(config, method, path, payload=None):
        assert method == "GET"
        if path.startswith("/api/jobs/"):
            return {"state": "error", "message": "计算中断"}
        ledger = _paper_status(cash=cash)["data"]
        ledger["advance_operation"] = operation
        return {"data": ledger}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "unknown"
    assert result["result"]["observed_date"] == proposal["baseline"]["as_of_date"]
    assert result["result"]["baseline_state_unchanged"] is False
    assert store.get_task(task["id"])["status"] == "needs_review"

    cash = 100_000
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "unknown"
    assert result["result"]["baseline_state_unchanged"] is True

    operation["state"] = "reviewed"
    result = await harness.reconcile(proposal["id"])
    assert result["status"] == "failed"
    assert store.get_task(task["id"])["status"] == "failed"


@pytest.mark.asyncio
async def test_failed_composite_job_keeps_child_advance_uncertain(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append((method, path))
        if method == "POST":
            return {"job_id": "job:composite-partial"}
        if path.startswith("/api/jobs/"):
            return {"state": "error", "message": "组合账户版本已变化"}
        if path == "/api/v2/paper/paper:child-one/status":
            return {"data": {"session": {"session_id": "paper:child-one", "last_date": "2026-09-26"},
                             "snapshot": {"as_of_date": "2026-09-26", "equity": 110000}}}
        if path == "/api/v2/paper/paper:child-two/status":
            return {"data": {"session": {"session_id": "paper:child-two", "last_date": "2026-09-25"},
                             "snapshot": {"as_of_date": "2026-09-25", "equity": 90000}}}
        ledger = _paper_status()["data"]
        ledger["kind"] = "composite"
        if store.get_proposal(proposal["id"])["status"] != "executing":
            ledger["session"]["params"] = {"child_session_ids": ["paper:child-one", "paper:child-two"]}
        return {"data": ledger}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    result = store.get_proposal(proposal["id"])
    assert sum(method == "POST" for method, _ in calls) == 1
    assert result["status"] == "unknown"
    assert result["result"]["job_id"] == "job:composite-partial"
    assert "组合模拟盘作业结果待核对" in result["result"]["error"]
    assert [(item["session_id"], item["as_of_date"]) for item in result["result"]["child_ledgers"]] == [
        ("paper:child-one", "2026-09-26"), ("paper:child-two", "2026-09-25")]
    assert store.get_task(task["id"])["status"] == "needs_review"
    assert (await harness.reconcile(proposal["id"]))["status"] == "unknown"
    assert sum(method == "POST" for method, _ in calls) == 1


@pytest.mark.asyncio
async def test_composite_child_audit_rejects_unsafe_account_ids(tmp_path, monkeypatch):
    harness, _, _, _ = await _proposed_advance(tmp_path)
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append((method, path))
        raise AssertionError("不应读取未经验证的子策略账户")

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    observed, error = await harness._read_composite_child_ledgers({
        "session": {"params": {"child_session_ids": ["paper:child-one", "../other"]}}
    }, "paper:advance")
    assert observed == []
    assert "未提供可核对" in error
    assert calls == []


@pytest.mark.asyncio
async def test_successful_non_trading_target_uses_receipt_date(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            return {"job_id": "job:weekend"}
        if path.startswith("/api/jobs/"):
            return {"state": "success", "result": {"data": {
                "session_id": "paper:advance", "last_date": "2026-09-25", "advanced_days": 0}}}
        return _paper_status()

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert store.get_proposal(proposal["id"])["status"] == "no_change"
    assert store.get_task(task["id"])["result"]["action"]["advanced_days"] == 0
    assert "账本未推进" in store.get_task(task["id"])["result"]["content"]
    assert store.list_events(task["id"])[-1]["event_type"] == "action_no_change"


@pytest.mark.asyncio
async def test_success_receipt_with_zero_days_and_new_date_needs_review(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            return {"job_id": "job:contradictory"}
        if path.startswith("/api/jobs/"):
            return {"state": "success", "result": {"data": {
                "session_id": "paper:advance", "last_date": "2026-09-26", "advanced_days": 0}}}
        as_of = "2026-09-26" if store.get_proposal(proposal["id"])["status"] == "submitted" else "2026-09-25"
        return _paper_status(as_of)

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert store.get_proposal(proposal["id"])["status"] == "unknown"
    assert store.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_preapproval_ledger_conflict_prevents_paper_write(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append(method)
        return {"data": {"session": {"session_id": "paper:advance", "last_date": "2026-09-26"},
                         "snapshot": {"as_of_date": "2026-09-25"}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert calls == ["GET"]
    assert store.get_proposal(proposal["id"])["status"] == "stale"
    assert "未执行" in store.get_task(task["id"])["result"]["content"]
