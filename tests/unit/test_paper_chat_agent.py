"""A bound paper conversation reads the ledger before synthesizing an answer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradingagents.core.chat_agent import ChatAgent
from tradingagents.core.tool_registry import LightweightTool, ToolRegistry
from tradingagents.skills.registry import SkillRegistry


def paper_agent():
    tools = ToolRegistry()
    handler = AsyncMock(return_value={
        "session_id": "paper:demo",
        "as_of_date": "2026-09-24",
        "source": "StockManager strategy paper ledger",
        "session": {"initial_cash": 100_000, "last_date": "2026-09-24"},
        "snapshot": {"as_of_date": "2026-09-24", "equity": 108_000, "cash": 20_000,
                     "positions": {"600519.SH": {"value": 88_000, "shares": 40}}},
        "decision": {"active_sleeve": "wfo", "switched": False},
        "next_plan": {"items": []},
        "recent_trades": [{"code": "600519.SH", "side": "BUY"}],
        "warnings": [],
    })
    tools.register(LightweightTool(
        name="get_paper_session", description="paper ledger",
        parameters={"type": "object", "properties": {"session_id": {"type": "string"}}},
        handler=handler,
    ))
    return ChatAgent({}, SkillRegistry(), tools), handler


@pytest.mark.asyncio
async def test_bound_paper_agent_reads_ledger_and_synthesizes_with_citation():
    agent, handler = paper_agent()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="当前权益 10.8 万元，未发生切换。"))
    with patch.object(agent, "_get_plain_llm", return_value=llm):
        result = await agent.handle(
            "为什么当前没有切换子策略？", session_id="chat-1",
            context={"paper_session_context": {"session_id": "paper:demo"}},
        )
    handler.assert_awaited_once_with(session_id="paper:demo")
    assert result.intent == "chat_answer"
    assert "未发生切换" in result.content
    assert result.citations[0]["as_of_date"] == "2026-09-24"
    messages = llm.ainvoke.await_args.args[0]
    assert "<paper_evidence>" in messages[-1].content
    assert "108000" in messages[-1].content
    assert "chat-1:paper:paper:demo" in agent._buffers


@pytest.mark.asyncio
async def test_bound_paper_agent_remains_useful_without_llm():
    agent, handler = paper_agent()
    with patch.object(agent, "_get_plain_llm", side_effect=RuntimeError("no key")):
        result = await agent.handle(
            "总结这个模拟盘", session_id="chat-1",
            context={"paper_session_context": {"session_id": "paper:demo"}},
        )
    handler.assert_awaited_once()
    assert "¥108,000.00" in result.content
    assert "模型不可用" in result.citations[0]["warnings"][-1]


@pytest.mark.asyncio
async def test_short_followup_still_refreshes_bound_paper_evidence():
    agent, handler = paper_agent()
    with patch.object(agent, "_get_plain_llm", side_effect=RuntimeError("no key")):
        for question in ("解释这个模拟盘", "继续"):
            await agent.handle(
                question, session_id="chat-1",
                context={"paper_session_context": {"session_id": "paper:demo"}},
            )
    assert handler.await_count == 2


@pytest.mark.asyncio
async def test_paper_agent_never_executes_a_chat_advance_request():
    agent, handler = paper_agent()
    with patch.object(agent, "_get_plain_llm", side_effect=RuntimeError("no key")):
        result = await agent.handle(
            "帮我推进到下一交易日", session_id="chat-1",
            context={"paper_session_context": {"session_id": "paper:demo"}},
        )
    handler.assert_awaited_once_with(session_id="paper:demo")
    assert "页面选择目标交易日并确认" in result.content
