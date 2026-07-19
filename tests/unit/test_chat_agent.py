"""Unit tests for ChatAgent intent classification.

All tests mock the LLM to return controlled responses so we do not need an
actual LLM provider or API key to verify the routing logic.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

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

    @pytest.mark.asyncio
    async def test_clarify_then_ticker_resolves_to_skill_run(self, chat_agent):
        """Chinese two-turn flow: '分析一下' clarifies, then the follow-up naming
        the stock resolves to skill_run; turn 2 must see turn 1's user text and
        the clarify reply in the message list."""
        clarify_resp = MagicMock()
        clarify_resp.content = ""
        clarify_resp.tool_calls = []
        skill_resp = MagicMock()
        skill_resp.content = ""
        skill_resp.tool_calls = [{"name": "stock_analysis", "args": {"ticker": "600519.SH"}}]
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=[clarify_resp, skill_resp])

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            first = await chat_agent.handle("分析一下", session_id="cn1")
            assert first.intent == "clarify"
            second = await chat_agent.handle("就茅台 600519.SH", session_id="cn1")
            assert second.intent == "skill_run"
            assert second.skill_id == "stock_analysis"

        second_msgs = mock_llm.ainvoke.await_args_list[1].args[0]
        humans = [m.content for m in second_msgs if isinstance(m, HumanMessage)]
        ais = [m.content for m in second_msgs if isinstance(m, AIMessage)]
        assert any("分析一下" in c for c in humans)
        assert any("茅台 600519.SH" in c for c in humans)
        assert any("请问您需要什么帮助" in c for c in ais)

    @pytest.mark.asyncio
    async def test_tool_answer_records_synthesized_turn(self, chat_agent):
        """A tool_answer has empty content; a synthesized note must be written to
        the buffer so the next turn knows a tool was already queried."""
        resp = MagicMock()
        resp.content = ""
        resp.tool_calls = [{"name": "get_portfolio_summary", "args": {}}]
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=resp)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            await chat_agent.handle("我的持仓怎么样", session_id="cn2")

        buf = chat_agent._buffers["cn2"]
        assert buf[-1]["role"] == "assistant"
        assert "get_portfolio_summary" in buf[-1]["content"]


class TestChatAgentErrorRouting:
    """Regression coverage for degraded / malformed routing paths."""

    @pytest.mark.asyncio
    async def test_lightweight_tool_handler_exception_degrades(self, chat_agent):
        """A tool handler that raises degrades to a tool_answer error payload
        instead of crashing the turn."""
        boom = LightweightTool(
            name="get_factor_snapshot",
            description="factor snapshot",
            parameters={"type": "object", "properties": {}},
            handler=AsyncMock(side_effect=RuntimeError("mcp down")),
            display="table",
        )
        chat_agent._tool_registry.register(boom)
        chat_agent._lightweight_names.add(boom.name)
        resp = MagicMock()
        resp.content = ""
        resp.tool_calls = [{"name": "get_factor_snapshot", "args": {}}]
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=resp)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("看下茅台的因子")

        assert result.intent == "tool_answer"
        assert result.tool_result["error"] == "mcp down"
        assert "出错" in result.content

    @pytest.mark.asyncio
    async def test_empty_tool_name_falls_back_to_chat(self, chat_agent):
        """A malformed tool_call with no resolvable name routes to chat_answer."""
        resp = MagicMock()
        resp.content = ""
        resp.tool_calls = [{"args": {"ticker": "600519.SH"}}]
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=resp)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("随便跑个东西")

        assert result.intent == "chat_answer"
        assert "不认识" in result.content

    @pytest.mark.asyncio
    async def test_tool_args_json_string_is_parsed(self, chat_agent):
        """Providers that return tool args as a JSON string still yield a parsed
        dict for skill_params."""
        resp = MagicMock()
        resp.content = ""
        resp.tool_calls = [{"name": "stock_analysis", "args": '{"ticker": "000001.SZ"}'}]
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=resp)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("分析平安银行")

        assert result.intent == "skill_run"
        assert result.skill_params == {"ticker": "000001.SZ"}

    @pytest.mark.asyncio
    async def test_lightweight_name_missing_from_registry_is_unavailable(self, chat_agent):
        """If a name is advertised as lightweight but the registry lacks it, the
        turn reports the tool unavailable rather than raising."""
        chat_agent._lightweight_names.add("ghost_tool")
        resp = MagicMock()
        resp.content = ""
        resp.tool_calls = [{"name": "ghost_tool", "args": {}}]
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=resp)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await chat_agent.handle("调用幽灵工具")

        assert result.intent == "chat_answer"
        assert "不可用" in result.content


class TestChatAgentSessionEviction:
    """Session buffer TTL + LRU eviction (mirrors LLMRouter)."""

    def test_stale_session_is_evicted(self, chat_agent):
        chat_agent._buffers["old"] = [{"role": "user", "content": "hi"}]
        chat_agent._buffer_ts["old"] = time.monotonic() - (chat_agent._session_ttl + 10)

        chat_agent._evict_stale_sessions()
        assert "old" not in chat_agent._buffers
        assert "old" not in chat_agent._buffer_ts

    def test_lru_eviction_when_over_capacity(self, chat_agent):
        chat_agent._max_sessions = 2
        now = time.monotonic()
        for i, sid in enumerate(["a", "b", "c"]):
            chat_agent._buffers[sid] = [{"role": "user", "content": sid}]
            chat_agent._buffer_ts[sid] = now + i  # 'a' is the oldest

        chat_agent._evict_stale_sessions()
        assert "a" not in chat_agent._buffers
        assert set(chat_agent._buffers) == {"b", "c"}
