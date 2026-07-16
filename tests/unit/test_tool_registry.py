"""Unit tests for ToolRegistry and lightweight tool handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tradingagents.core.tool_registry import LightweightTool, ToolRegistry


class TestToolRegistry:
    """Tests for ToolRegistry registration, lookup, and schema generation."""

    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = LightweightTool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}},
            handler=AsyncMock(return_value={"ok": True}),
            display="text",
        )
        registry.register(tool)
        assert registry.get("test_tool") is tool
        assert registry.get("nonexistent") is None

    def test_register_duplicate_raises(self):
        registry = ToolRegistry()
        tool = LightweightTool(
            name="dup", description="D", parameters={}, handler=AsyncMock()
        )
        registry.register(tool)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool)

    def test_list_all(self):
        registry = ToolRegistry()
        t1 = LightweightTool(name="a", description="A", parameters={}, handler=AsyncMock())
        t2 = LightweightTool(name="b", description="B", parameters={}, handler=AsyncMock())
        registry.register(t1)
        registry.register(t2)
        all_tools = registry.list_all()
        assert len(all_tools) == 2
        names = {t.name for t in all_tools}
        assert names == {"a", "b"}

    def test_to_openai_schemas(self):
        registry = ToolRegistry()
        tool = LightweightTool(
            name="test",
            description="Test tool",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=AsyncMock(),
            display="table",
        )
        registry.register(tool)
        schemas = registry.to_openai_schemas()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "test"
        assert schemas[0]["function"]["description"] == "Test tool"
        assert schemas[0]["function"]["parameters"]["type"] == "object"

    def test_to_openai_schemas_empty(self):
        registry = ToolRegistry()
        assert registry.to_openai_schemas() == []


class TestLightweightToolHandlers:
    """Tests for individual lightweight tool handlers."""

    @pytest.mark.asyncio
    async def test_get_portfolio_summary_with_holdings(self):
        from tradingagents.core.lightweight_tools import make_get_portfolio_summary

        mock_db = MagicMock()
        mock_db.list_holdings.return_value = [
            {"symbol": "600519.SH", "name": "贵州茅台", "quantity": 100, "cost_basis": 1800, "current_price": 1900},
        ]
        handler = make_get_portfolio_summary(mock_db)
        result = await handler()
        assert result["total_symbols"] == 1
        assert result["holdings"][0]["symbol"] == "600519.SH"
        assert result["holdings"][0]["pnl"] == 10000.0

    @pytest.mark.asyncio
    async def test_get_portfolio_summary_empty(self):
        from tradingagents.core.lightweight_tools import make_get_portfolio_summary

        mock_db = MagicMock()
        mock_db.list_holdings.return_value = []
        handler = make_get_portfolio_summary(mock_db)
        result = await handler()
        assert result["holdings"] == []
        assert result["total_symbols"] == 0

    @pytest.mark.asyncio
    async def test_get_portfolio_summary_error(self):
        from tradingagents.core.lightweight_tools import make_get_portfolio_summary

        mock_db = MagicMock()
        mock_db.list_holdings.side_effect = RuntimeError("DB down")
        handler = make_get_portfolio_summary(mock_db)
        result = await handler()
        assert "error" in result
        assert "DB down" in result["error"]

    @pytest.mark.asyncio
    async def test_search_artifacts_found(self):
        from tradingagents.core.lightweight_tools import make_search_artifacts

        mock_db = MagicMock()
        mock_db.list_artifacts.return_value = [
            {"id": "a1", "title": "茅台分析", "summary": "buy signal", "artifact_type": "report", "subject_id": "600519.SH", "subject_name": "茅台", "created_at": "2026-01-01"},
        ]
        handler = make_search_artifacts(mock_db)
        result = await handler(q="茅台", limit=10)
        assert result["total"] == 1
        assert result["results"][0]["title"] == "茅台分析"

    @pytest.mark.asyncio
    async def test_search_artifacts_not_found(self):
        from tradingagents.core.lightweight_tools import make_search_artifacts

        mock_db = MagicMock()
        mock_db.list_artifacts.return_value = []
        handler = make_search_artifacts(mock_db)
        result = await handler(q="xyz")
        assert result["results"] == []
        assert "No artifacts" in result["message"]

    @pytest.mark.asyncio
    async def test_get_recent_runs(self):
        from tradingagents.core.lightweight_tools import make_get_recent_runs

        mock_db = MagicMock()
        mock_db.list_runs.return_value = [
            {"id": "r1", "skill_id": "daily_pipeline", "status": "completed", "params_json": "{}", "created_at": "2026-01-01"},
        ]
        handler = make_get_recent_runs(mock_db)
        result = await handler(limit=5)
        assert result["total"] == 1
        assert result["runs"][0]["id"] == "r1"

    @pytest.mark.asyncio
    async def test_get_mcp_factor_snapshot_no_ts_code(self):
        from tradingagents.core.lightweight_tools import make_get_mcp_factor_snapshot

        handler = make_get_mcp_factor_snapshot({})
        result = await handler(ts_code="")
        assert "error" in result
        assert "ts_code" in result["error"]

    @pytest.mark.asyncio
    async def test_get_mcp_factor_snapshot_mcp_unavailable(self):
        from tradingagents.core.lightweight_tools import make_get_mcp_factor_snapshot

        # MCP disabled → get_mcp_client returns None → "not connected" path.
        handler = make_get_mcp_factor_snapshot({"stockmanager_mcp_enabled": False})
        result = await handler(ts_code="600519.SH")
        assert "error" in result
        assert "not connected" in result["error"]

    @pytest.mark.asyncio
    async def test_get_strategy_lessons(self):
        from tradingagents.core.lightweight_tools import make_get_strategy_lessons

        mock_db = MagicMock()
        mock_db.list_strategy_lessons.return_value = [
            {"id": "l1", "lesson_type": "ex_ante_miss", "scope": "global", "finding": "高估值+资金流缺失胜率下降", "suggested_adjustment": "降低权重", "confidence": "high", "evidence_count": 3, "created_at": "2026-01-01"},
        ]
        handler = make_get_strategy_lessons(mock_db)
        result = await handler()
        assert result["total"] == 1
        assert result["lessons"][0]["id"] == "l1"
