"""Durable Agent task lifecycle and account evidence boundaries."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tradingagents.core.agent_harness import (
    AgentStore,
    TradingAgentHarness,
    _answer_evidence_result,
)
from tradingagents.core.chat_agent import ChatResponse
from tradingagents.core.persistence import Database
from tradingagents.core.stockmanager_paper import PaperServiceError
from tradingagents.core.tool_registry import LightweightTool, ToolRegistry


class _Chat:
    def __init__(self, response=None):
        self.response = response or ChatResponse(intent="chat_answer", content="普通回答")

    async def handle(self, *_args, **_kwargs):
        return self.response


class _Skills:
    def get(self, _name):
        raise AssertionError("写入型 Skill 不得启动")


def test_missing_factor_score_is_masked_only_in_model_input():
    result = {"snapshot": {"rows": [{"data_coverage": {"valuation": "available", "flow": "missing"},
                                    "factor_scores": {"valuation": 52.0, "flow": 50.0}}]}}
    safe = _answer_evidence_result({"tool_name": "get_mcp_factor_snapshot", "result": result})
    assert safe["snapshot"]["rows"][0]["factor_scores"] == {"valuation": 52.0, "flow": None}
    assert result["snapshot"]["rows"][0]["factor_scores"]["flow"] == 50.0


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
async def test_paper_task_persists_events_and_evidence_across_store_reopen(tmp_path):
    calls = []

    async def paper(session_id):
        calls.append(session_id)
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25", "snapshot": {
                    "equity": 1000000, "cash": 500000, "positions": {"600519.SH": {}}},
                "recent_trades": []}

    harness, store = _harness(tmp_path, paper_handler=paper)

    async def synthesize(*_args):
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
    conversation = store.create_conversation("测试", "paper-3")
    task = harness.submit(conversation["id"], "解释策略")
    await asyncio.wait_for(started.wait(), timeout=2)
    with pytest.raises(ValueError, match="运行中"):
        harness.submit(conversation["id"], "第二个问题")
    assert await harness.cancel(task["id"])
    await harness._active[task["id"]]
    assert store.get_task(task["id"])["status"] == "cancelled"


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
        ("search_artifacts", {"q": "策略报告", "limit": 5}),
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
        return {"ts_code": ts_code, "source": "StockManager MCP",
                "as_of_date": "2026-09-25", "snapshot": {"valuation": 12.0}}

    harness.tools.register(LightweightTool(
        name="get_mcp_factor_snapshot", description="factor", parameters={},
        handler=factor,
    ))

    async def synthesize(*_args):
        return "截至 2026-09-25 的因子快照已读取。"

    harness._synthesize = synthesize
    conversation = store.create_conversation("测试", None)
    task = harness.submit(conversation["id"], "看 600519.sh 估值")
    await harness._active[task["id"]]

    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == ["600519.SH"]
    assert detail["evidence"][0]["as_of_date"] == "2026-09-25"
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

    async def synthesize(*_args):
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
        handler=factor,
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
        handler=factor,
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

    async def synthesize(*_args):
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

    async def synthesize(*_args):
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
        handler=announcements,
    ))
    conversation = store.create_conversation("测试", "paper:mine")
    task = harness.submit(conversation["id"], "模拟盘 600519.SH 的问询公告如何？")
    await harness._active[task["id"]]
    detail = store.conversation_detail(conversation["id"])["tasks"][0]
    assert calls == [("600519.SH", "2026-09-25")]
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
async def test_failed_required_portfolio_source_stays_abstained_after_replan(tmp_path):
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
    assert [item["tool_name"] for item in detail["evidence"]] == [
        "get_portfolio_summary", "search_artifacts",
    ]
    assert any(event["event_type"] == "plan_revised" for event in detail["events"])
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
        return {"session_id": session_id, "source": "StockManager ledger",
                "as_of_date": "2026-09-25", "snapshot": {"equity": 100000, "positions": {}}}

    harness, store = _harness(tmp_path, paper_handler=paper)
    conversation = store.create_conversation("推进测试", "paper:advance")
    task = harness.submit(conversation["id"], "推进模拟盘到 2026-09-28")
    await harness._active[task["id"]]
    proposal = store.proposal_for_task(task["id"])
    assert store.get_task(task["id"])["status"] == "awaiting_approval"
    assert proposal["args"] == {"target_date": "2026-09-28"}
    return harness, store, task, proposal


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
async def test_advance_requires_one_time_approval_and_reconciles_ledger(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    calls = []

    async def paper_request(config, method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST":
            return {"job_id": "job:one"}
        if path.endswith("/status"):
            date_value = "2026-09-28" if any(call[0] == "POST" for call in calls) else "2026-09-25"
            return {"data": {"snapshot": {"as_of_date": date_value, "equity": 101000}}}
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
async def test_uncertain_post_never_retries(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)
    posts = 0

    async def uncertain(config, method, path, payload=None):
        nonlocal posts
        if method == "POST":
            posts += 1
            raise PaperServiceError("连接中断")
        return {"data": {"snapshot": {"as_of_date": "2026-09-25"}}}

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
        return {"data": {"snapshot": {"as_of_date": "2026-09-28", "equity": 102000}}}

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
async def test_success_receipt_must_match_account_ledger(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            return {"job_id": "job:other"}
        if path.startswith("/api/jobs/"):
            return {"state": "success", "result": {"data": {
                "session_id": "paper:someone-else", "last_date": "2026-09-28"}}}
        as_of = "2026-09-28" if store.get_proposal(proposal["id"])["status"] == "submitted" else "2026-09-25"
        return {"data": {"snapshot": {"as_of_date": as_of}}}

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
        return {"data": {"snapshot": {"as_of_date": "2026-09-25"}}}

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
        return {"data": {"snapshot": {"as_of_date": as_of}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    result = store.get_proposal(proposal["id"])
    assert result["status"] == "unknown"
    assert result["result"]["observed_date"] == "2026-09-26"
    assert store.get_task(task["id"])["status"] == "needs_review"


@pytest.mark.asyncio
async def test_successful_non_trading_target_uses_receipt_date(tmp_path, monkeypatch):
    harness, store, task, proposal = await _proposed_advance(tmp_path)

    async def paper_request(config, method, path, payload=None):
        if method == "POST":
            return {"job_id": "job:weekend"}
        if path.startswith("/api/jobs/"):
            return {"state": "success", "result": {"data": {
                "session_id": "paper:advance", "last_date": "2026-09-25", "advanced_days": 0}}}
        return {"data": {"snapshot": {"as_of_date": "2026-09-25", "equity": 100000}}}

    monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
    harness.approve(proposal["id"])
    await harness._active[task["id"]]
    assert store.get_proposal(proposal["id"])["status"] == "completed"
    assert store.get_task(task["id"])["result"]["action"]["advanced_days"] == 0
