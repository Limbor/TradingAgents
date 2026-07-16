"""Unit tests for ChatAgent intent classification.

All tests mock the LLM to return controlled responses so we do not need an
actual LLM provider or API key to verify the routing logic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from tradingagents.core.chat_agent import ChatAgent
from tradingagents.core.tool_registry import LightweightTool, ToolRegistry
from tradingagents.skills.base import BaseSkill, SkillMetadata
from tradingagents.skills.registry import SkillRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_skill_registry() -> SkillRegistry:
    """Return a SkillRegistry with one registered skill."""
    reg = SkillRegistry()
    skill = MagicMock(spec=BaseSkill)
    skill.metadata = SkillMetadata(
        id="stock_analysis",
        name="Stock Analysis",
        description="Analyze a stock",
        version="1.0",
        triggers=["分析"],
    )
    reg.register(skill)
    return reg


@pytest.fixture
def mock_tool_registry() -> ToolRegistry:
    """Return a ToolRegistry with one lightweight tool."""
    reg = ToolRegistry()
    tool = LightweightTool(
        name="get_portfolio_summary",
        description="Get portfolio holdings",
        parameters={"type": "object", "properties": {}},
        handler=AsyncMock(return_value={"holdings": [], "total_symbols": 0}),
        display="table",
    )
    reg.register(tool)
    return reg


@pytest.fixture
def chat_agent(mock_skill_registry, mock_tool_registry):
    """Return a ChatAgent with mock registries and a mocked LLM."""
    config = {
        "llm_provider": "openai",
        "quick_think_llm": "gpt-5.4-mini",
    }
    return ChatAgent(config, mock_skill_registry, mock_tool_registry)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChatAgentIntentClassification:
    """Verify ChatAgent correctly classifies LLM responses into 4 intents."""

    @pytest.mark.asyncio
    async def test_chat_answer_intent(self, chat_agent):
        """LLM responds with content, no tool_call → chat_answer."""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "WATCHLIST stands for Watch List, a monitoring category."
        mock_response.tool_calls = []
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("解释一下 WATCHLIST 是什么意思")
            assert result.intent == "chat_answer"
            assert "WATCHLIST" in result.content

    @pytest.mark.asyncio
    async def test_clarify_intent_empty_content(self, chat_agent):
        """LLM responds with empty content, no tool_call → clarify."""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.tool_calls = []
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("分析一下")
            assert result.intent == "clarify"

    @pytest.mark.asyncio
    async def test_tool_answer_intent(self, chat_agent):
        """LLM calls a lightweight tool → tool_answer."""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.tool_calls = [{
            "name": "get_portfolio_summary",
            "args": {},
        }]
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("我的持仓怎么样")
            assert result.intent == "tool_answer"
            assert result.tool_name == "get_portfolio_summary"
            assert result.tool_display == "table"
            assert result.tool_result is not None
            assert "返回 0 条结果" in result.content

    @pytest.mark.asyncio
    async def test_multiple_lightweight_tools_are_combined(self, chat_agent):
        second = LightweightTool(
            name="get_strategy_lessons",
            description="Get lessons",
            parameters={"type": "object", "properties": {}},
            handler=AsyncMock(return_value={"lessons": [{"id": "l1"}]}),
            display="text",
        )
        chat_agent._tool_registry.register(second)
        chat_agent._lightweight_names.add(second.name)
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.tool_calls = [
            {"name": "get_portfolio_summary", "args": {}},
            {"name": "get_strategy_lessons", "args": {}},
        ]
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("结合持仓和策略经验给建议")

        assert result.intent == "tool_answer"
        assert result.tool_name == "multi_tool"
        assert set(result.tool_result["results"]) == {
            "get_portfolio_summary",
            "get_strategy_lessons",
        }
        assert len(result.citations) == 2

    @pytest.mark.asyncio
    async def test_skill_run_intent(self, chat_agent):
        """LLM calls a skill tool → skill_run."""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.tool_calls = [{
            "name": "stock_analysis",
            "args": {"ticker": "600519.SH"},
        }]
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("分析茅台")
            assert result.intent == "skill_run"
            assert result.skill_id == "stock_analysis"
            assert result.skill_params == {"ticker": "600519.SH"}

    @pytest.mark.asyncio
    async def test_unknown_tool_fallback(self, chat_agent):
        """LLM calls an unknown tool → fallback to chat_answer."""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = ""
        mock_response.tool_calls = [{
            "name": "nonexistent_tool",
            "args": {},
        }]
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("do something weird")
            assert result.intent == "chat_answer"
            assert "不认识" in result.content

    @pytest.mark.asyncio
    async def test_llm_timeout(self, chat_agent):
        """LLM timeout → graceful fallback chat_answer."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=TimeoutError)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("hello")
            assert result.intent == "chat_answer"
            assert "超时" in result.content or "timeout" in result.content.lower()

    @pytest.mark.asyncio
    async def test_llm_error(self, chat_agent):
        """LLM error → graceful fallback chat_answer."""
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=RuntimeError("API error"))

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("hello")
            assert result.intent == "chat_answer"
            assert "出错" in result.content or "error" in result.content.lower()


class TestChatAgentSessionManagement:
    """Verify session buffer management."""

    def test_clear_session(self, chat_agent):
        chat_agent._buffers["session1"] = [{"role": "user", "content": "hello"}]
        chat_agent._buffer_ts["session1"] = 12345

        chat_agent.clear_session("session1")
        assert "session1" not in chat_agent._buffers
        assert "session1" not in chat_agent._buffer_ts

    def test_clear_nonexistent_session(self, chat_agent):
        # Should not raise
        chat_agent.clear_session("nonexistent")


class TestChatAgentMultiTurnContext:
    """Verify assistant replies are stored back into the session buffer so the
    LLM can see its own prior turns in multi-turn conversations (P0-4 fix)."""

    @pytest.mark.asyncio
    async def test_assistant_reply_stored_in_buffer(self, chat_agent):
        """After a chat_answer, the buffer should hold [user, assistant]."""
        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = "WATCHLIST 表示关注列表。"
        mock_response.tool_calls = []
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            await chat_agent.handle("解释一下 WATCHLIST", session_id="s1")

        buf = chat_agent._buffers["s1"]
        assert len(buf) == 2
        assert buf[0]["role"] == "user"
        assert buf[1]["role"] == "assistant"
        assert "WATCHLIST" in buf[1]["content"]

    @pytest.mark.asyncio
    async def test_second_turn_sees_prior_assistant_message(self, chat_agent):
        """The messages sent to the LLM on turn 2 must include turn 1's assistant reply."""
        mock_llm = AsyncMock()
        first_response = MagicMock()
        first_response.content = "茅台是贵州茅台的简称。"
        first_response.tool_calls = []
        second_response = MagicMock()
        second_response.content = "五粮液是另一种白酒。"
        second_response.tool_calls = []
        mock_llm.ainvoke = AsyncMock(side_effect=[first_response, second_response])

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            await chat_agent.handle("茅台是什么", session_id="s2")
            await chat_agent.handle("那五粮液呢", session_id="s2")

        # The second ainvoke call should have received an AIMessage with the
        # first assistant reply in the messages list.
        second_call_messages = mock_llm.ainvoke.await_args_list[1].args[0]
        assistant_contents = [
            m.content for m in second_call_messages
            if isinstance(m, AIMessage)
        ]
        assert any("茅台是贵州茅台" in c for c in assistant_contents), (
            "second turn did not see the first assistant reply — multi-turn context broken"
        )
