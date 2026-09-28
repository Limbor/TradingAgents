"""Unit tests for ChatAgent intent classification.

All tests mock the LLM to return controlled responses so we do not need an
actual LLM provider or API key to verify the routing logic.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.core.chat_agent import ChatAgent, _compact_context
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
    async def test_text_only_mode_never_executes_tools(self, chat_agent, mock_tool_registry):
        plain = AsyncMock()
        plain.ainvoke = AsyncMock(return_value=AIMessage(
            content="当前账本需要另行核对。",
            tool_calls=[{"name": "get_portfolio_summary", "args": {}, "id": "tool-1"}],
        ))
        with (patch.object(chat_agent, "_get_plain_llm", return_value=plain),
              patch.object(chat_agent, "_get_llm_with_tools",
                           side_effect=AssertionError("不得绑定工具"))):
            result = await chat_agent.handle(
                "当前模拟盘权益是多少？", context={"paper_session_context": {"session_id": "paper:mine"}},
                allow_tools=False,
            )

        assert result.intent == "chat_answer"
        assert result.content == "当前账本需要另行核对。"
        mock_tool_registry.get("get_portfolio_summary").handler.assert_not_awaited()
        assert "本轮没有开放工具调用" in plain.ainvoke.await_args.args[0][0].content

    @pytest.mark.asyncio
    async def test_paper_tool_call_uses_page_bound_account(self, chat_agent, mock_tool_registry):
        paper_handler = AsyncMock(return_value={"session_id": "paper:mine", "as_of_date": "2026-09-25"})
        mock_tool_registry.register(LightweightTool(
            name="get_paper_session", description="paper", parameters={}, handler=paper_handler,
        ))
        chat_agent = ChatAgent(chat_agent._config, chat_agent._skill_registry, mock_tool_registry)
        response = MagicMock(content="", tool_calls=[{
            "name": "get_paper_session", "args": {"session_id": "paper:other"},
        }])
        model = AsyncMock(ainvoke=AsyncMock(return_value=response))
        with patch.object(chat_agent, "_get_llm_with_tools", return_value=model):
            result = await chat_agent.handle(
                "扫描市场", context={"paper_session_context": {"session_id": "paper:mine"}},
            )

        paper_handler.assert_awaited_once_with(session_id="paper:mine")
        assert result.intent == "tool_answer"
        assert result.tool_args == {"session_id": "paper:mine"}
        assert result.citations[0]["args"] == {"session_id": "paper:mine"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("include_portfolio", [False, True])
    async def test_paper_tool_call_without_page_scope_asks_for_account(
        self, chat_agent, mock_tool_registry, include_portfolio,
    ):
        paper_handler = AsyncMock()
        mock_tool_registry.register(LightweightTool(
            name="get_paper_session", description="paper", parameters={}, handler=paper_handler,
        ))
        chat_agent = ChatAgent(chat_agent._config, chat_agent._skill_registry, mock_tool_registry)
        calls = [{"name": "get_paper_session", "args": {"session_id": "paper:other"}}]
        if include_portfolio:
            calls.append({"name": "get_portfolio_summary", "args": {}})
        model = AsyncMock(ainvoke=AsyncMock(return_value=MagicMock(content="", tool_calls=calls)))
        with patch.object(chat_agent, "_get_llm_with_tools", return_value=model):
            result = await chat_agent.handle("你好")

        assert result.intent == "clarify"
        assert "选择要查询的模拟盘账户" in result.content
        paper_handler.assert_not_awaited()
        mock_tool_registry.get("get_portfolio_summary").handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_legacy_chat_cannot_execute_registered_write_tool(
        self, chat_agent, mock_tool_registry,
    ):
        write_handler = AsyncMock(return_value={"ok": True})
        mock_tool_registry.register(LightweightTool(
            name="paper_write_probe", description="write", parameters={"type": "object"},
            handler=write_handler, permission="paper_write",
        ))
        chat_agent = ChatAgent(chat_agent._config, chat_agent._skill_registry, mock_tool_registry)
        model = AsyncMock(ainvoke=AsyncMock(return_value=MagicMock(
            content="", tool_calls=[{"name": "paper_write_probe", "args": {}}],
        )))
        with patch.object(chat_agent, "_get_llm_with_tools", return_value=model):
            result = await chat_agent.handle("请修改模拟盘")

        assert result.intent == "chat_answer"
        assert "核对范围与权限" in result.content
        write_handler.assert_not_awaited()

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


class TestSanitizeSkillParams:
    """LLM-hallucinated date params must be stripped when the user gave no date."""

    def test_drops_hallucinated_trade_date(self):
        from tradingagents.core.chat_agent import ChatAgent

        params = {"trade_date": "2025-07-08", "industry_top_n": 30}
        cleaned = ChatAgent._sanitize_skill_params("分析半导体板块", params)
        assert "trade_date" not in cleaned
        assert cleaned["industry_top_n"] == 30

    def test_keeps_date_when_user_mentioned_one(self):
        from tradingagents.core.chat_agent import ChatAgent

        for text in ("看下2026-07-24的市场", "复盘7月24日行情", "20260724选股"):
            params = {"trade_date": "2026-07-24"}
            assert ChatAgent._sanitize_skill_params(text, params) == params

    def test_no_date_params_passthrough(self):
        from tradingagents.core.chat_agent import ChatAgent

        params = {"limit": 5, "industries": ["半导体"]}
        assert ChatAgent._sanitize_skill_params("选5只半导体股", params) == params


class TestChatAgentPageContext:
    """Page context is compacted, injected for the current turn only, and
    never written raw into the session buffer."""

    @pytest.mark.asyncio
    async def test_context_injected_into_llm_and_not_into_buffer(self, chat_agent):
        mock_llm = AsyncMock()
        resp = MagicMock()
        resp.content = "好的。"
        resp.tool_calls = []
        mock_llm.ainvoke = AsyncMock(return_value=resp)

        context = {
            "holding_context": {"symbol": "600519.SH", "quantity": 100, "avg_cost": 1500.0},
            "ignored_key": {"foo": "bar"},
        }
        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            await chat_agent.handle("这只持仓怎么样", session_id="ctx1", context=context)

        messages = mock_llm.ainvoke.await_args.args[0]
        last_human = [m for m in messages if isinstance(m, HumanMessage)][-1]
        # Injected outside <user_input> as a <page_context> block…
        assert "<page_context>" in last_human.content
        assert "600519.SH" in last_human.content
        assert last_human.content.index("</user_input>") < last_human.content.index("<page_context>")
        # …with non-whitelisted keys dropped.
        assert "ignored_key" not in last_human.content

        # The buffer keeps a one-line summary marker, never the raw JSON.
        buf = chat_agent._buffers["ctx1"]
        assert "avg_cost" not in buf[0]["content"]
        assert "携带页面上下文" in buf[0]["content"]
        assert "holding_context(600519.SH)" in buf[0]["content"]

    @pytest.mark.asyncio
    async def test_no_context_messages_unchanged(self, chat_agent):
        mock_llm = AsyncMock()
        resp = MagicMock()
        resp.content = "回答。"
        resp.tool_calls = []
        mock_llm.ainvoke = AsyncMock(return_value=resp)

        with patch.object(chat_agent, "_get_llm_with_tools", return_value=mock_llm):
            await chat_agent.handle("你好", session_id="ctx2")

        messages = mock_llm.ainvoke.await_args.args[0]
        last_human = [m for m in messages if isinstance(m, HumanMessage)][-1]
        assert last_human.content == "<user_input>你好</user_input>"
        assert chat_agent._buffers["ctx2"][0]["content"] == "你好"

    def test_compact_context_budgets(self):
        # Long strings truncated to 300 chars, lists to 5 items, total to 4KB.
        context = {
            "news_context": {
                "title": "T" * 1000,
                "symbols": [f"SYM{i}" for i in range(20)],
            },
            "unrelated": {"x": 1},
        }
        text = _compact_context(context)
        assert len(text) <= 4096
        assert "T" * 301 not in text
        assert "T" * 300 in text
        assert text.count("SYM") == 5
        assert "unrelated" not in text

    def test_compact_context_empty_cases(self):
        assert _compact_context(None) == ""
        assert _compact_context({}) == ""
        assert _compact_context({"other": {"a": 1}}) == ""


class TestNewsContextMisrouteGuard:
    """A news-interpretation turn must never launch the market_overview
    snapshot pipeline: on misroute the agent retries without tools."""

    @pytest.fixture
    def agent_with_market_overview(self, mock_tool_registry):
        reg = SkillRegistry()
        for skill_id in ("stock_analysis", "market_overview", "daily_pipeline"):
            skill = MagicMock(spec=BaseSkill)
            skill.metadata = SkillMetadata(
                id=skill_id, name=skill_id, description="d", version="1.0", triggers=[],
            )
            reg.register(skill)
        config = {"llm_provider": "openai", "quick_think_llm": "gpt-5.4-mini"}
        return ChatAgent(config, reg, mock_tool_registry)

    @staticmethod
    def _tool_call_response(tool_name: str):
        resp = MagicMock()
        resp.content = ""
        resp.tool_calls = [{"name": tool_name, "args": {}}]
        return resp

    @pytest.mark.asyncio
    async def test_misroute_with_news_context_forces_text_answer(
        self, agent_with_market_overview
    ):
        agent = agent_with_market_overview
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=self._tool_call_response("market_overview")
        )
        plain = AsyncMock()
        plain_resp = MagicMock()
        plain_resp.content = "这条新闻利好AI算力产业链。"
        plain_resp.tool_calls = []
        plain.ainvoke = AsyncMock(return_value=plain_resp)

        context = {"news_context": {"title": "微软股价大涨", "polarity": "bullish"}}
        with (
            patch.object(agent, "_get_llm_with_tools", return_value=mock_llm),
            patch.object(agent, "_get_plain_llm", return_value=plain),
        ):
            result = await agent.handle(
                "这条新闻对市场有什么影响：微软股价大涨", context=context
            )

        assert result.intent == "chat_answer"
        assert "利好" in result.content
        plain.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_market_overview_still_runs_without_news_context(
        self, agent_with_market_overview
    ):
        agent = agent_with_market_overview
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=self._tool_call_response("market_overview")
        )
        with patch.object(agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await agent.handle("重新生成市场全景")

        assert result.intent == "skill_run"
        assert result.skill_id == "market_overview"

    @pytest.mark.asyncio
    async def test_qualitative_sector_misroute_is_overridden(
        self, agent_with_market_overview
    ):
        agent = agent_with_market_overview
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=self._tool_call_response("daily_pipeline")
        )
        with patch.object(agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await agent.handle("分析今天钨板块的驱动逻辑和持续性")

        assert result.intent == "skill_run"
        assert result.skill_id == "market_overview"
        assert result.skill_params == {"focus_industries": ["钨"]}

    @pytest.mark.asyncio
    async def test_sector_stock_selection_is_not_overridden(
        self, agent_with_market_overview
    ):
        agent = agent_with_market_overview
        mock_llm = AsyncMock()
        response = self._tool_call_response("daily_pipeline")
        response.tool_calls[0]["args"] = {"industries": ["煤炭"]}
        mock_llm.ainvoke = AsyncMock(return_value=response)
        with patch.object(agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await agent.handle("筛选煤炭板块的股票")

        assert result.intent == "skill_run"
        assert result.skill_id == "daily_pipeline"

    @pytest.mark.asyncio
    async def test_other_skill_with_news_context_not_blocked(
        self, agent_with_market_overview
    ):
        """Guard is narrow: only the snapshot pipeline is overridden."""
        agent = agent_with_market_overview
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=self._tool_call_response("stock_analysis")
        )
        context = {"news_context": {"title": "茅台提价"}}
        with patch.object(agent, "_get_llm_with_tools", return_value=mock_llm):
            result = await agent.handle("深度分析下贵州茅台", context=context)

        assert result.intent == "skill_run"
        assert result.skill_id == "stock_analysis"

    @pytest.mark.asyncio
    async def test_plain_fallback_failure_returns_apology(
        self, agent_with_market_overview
    ):
        agent = agent_with_market_overview
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=self._tool_call_response("market_overview")
        )
        plain = AsyncMock()
        plain.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))

        context = {"news_context": {"title": "微软股价大涨"}}
        with (
            patch.object(agent, "_get_llm_with_tools", return_value=mock_llm),
            patch.object(agent, "_get_plain_llm", return_value=plain),
        ):
            result = await agent.handle("这条新闻对市场有什么影响", context=context)

        assert result.intent == "chat_answer"
        assert "无法生成" in result.content
