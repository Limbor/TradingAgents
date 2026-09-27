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
    async def test_paper_handler_rejects_mismatched_account_before_other_reads(self, monkeypatch):
        from tradingagents.core.lightweight_tools import make_get_paper_session

        paths = []

        async def paper_request(_config, _method, path):
            paths.append(path)
            return {"data": {"session": {"session_id": "paper:other",
                                         "last_date": "2026-09-25"},
                             "snapshot": {"as_of_date": "2026-09-25"}}}

        monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
        result = await make_get_paper_session({})("paper:mine")
        assert "账户" in result["error"]
        assert paths == ["/api/v2/paper/paper:mine/status"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("plan, expected_current", [
        ({"signal_date": "2026-09-25", "items": []}, True),
        ({"signal_date": "2026-09-24", "items": []}, False),
        ({"signal_date": None, "items": [], "error": "engine build failed"}, False),
        (None, False),
    ])
    async def test_paper_handler_checks_plan_date_against_ledger(
        self, monkeypatch, plan, expected_current,
    ):
        from tradingagents.core.lightweight_tools import make_get_paper_session

        async def paper_request(_config, _method, path):
            if path.endswith("/status"):
                return {"data": {"session": {"session_id": "paper:mine",
                                             "last_date": "2026-09-25"},
                                 "snapshot": {"as_of_date": "2026-09-25", "equity": 100000}}}
            if path.endswith("/next_plan"):
                return {"data": plan}
            if path.endswith("/trades?limit=30"):
                return {"items": []}
            if path.endswith("/equity_curve"):
                return {"data": {"daily_records": []}}
            raise AssertionError(path)

        monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
        result = await make_get_paper_session({})("paper:mine")
        assert result["freshness"].get("is_active_plan_current") is expected_current
        assert bool(result["warnings"]) is (expected_current is False)
        assert result["next_plan"] == plan

    @pytest.mark.asyncio
    async def test_paper_handler_preserves_upstream_stale_flag(self, monkeypatch):
        from tradingagents.core.lightweight_tools import make_get_paper_session

        async def paper_request(_config, _method, path):
            if path.endswith("/status"):
                return {"data": {"session": {"session_id": "paper:mine",
                                             "last_date": "2026-09-25"},
                                 "freshness": {"is_active_plan_current": False}}}
            if path.endswith("/next_plan"):
                return {"data": {"signal_date": "2026-09-25", "items": []}}
            if path.endswith("/trades?limit=30"):
                return {"items": []}
            if path.endswith("/equity_curve"):
                return {"data": {"daily_records": []}}
            raise AssertionError(path)

        monkeypatch.setattr("tradingagents.core.stockmanager_paper.paper_request", paper_request)
        result = await make_get_paper_session({})("paper:mine")
        assert result["freshness"]["is_active_plan_current"] is False
        assert any("不是最新版本" in warning for warning in result["warnings"])

    @pytest.mark.asyncio
    async def test_get_portfolio_summary_with_holdings(self):
        from tradingagents.core.lightweight_tools import make_get_portfolio_summary

        mock_db = MagicMock()
        mock_db.list_holdings.return_value = [
            {"symbol": "600519.SH", "name": "贵州茅台", "quantity": 100,
             "avg_cost": 1800, "current_price": 1900,
             "updated_at": "2026-09-20T08:00:00+00:00"},
        ]
        handler = make_get_portfolio_summary(mock_db)
        result = await handler()
        assert result["total_symbols"] == 1
        assert result["holdings"][0]["symbol"] == "600519.SH"
        assert result["holdings"][0]["pnl"] == 10000.0
        assert result["holdings"][0]["cost_basis"] == 1800
        assert result["holdings"][0]["record_updated_at"] == "2026-09-20T08:00:00+00:00"
        assert "未核对实时行情" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_portfolio_summary_uses_persisted_cost_field(self, tmp_path):
        from tradingagents.core.lightweight_tools import make_get_portfolio_summary
        from tradingagents.core.persistence import Database

        db = Database(tmp_path / "holdings.db")
        db.upsert_holding("600519.SH", quantity=100, avg_cost=1800,
                          current_price=1900)
        result = await make_get_portfolio_summary(db)()
        assert result["holdings"][0]["cost_basis"] == 1800
        assert result["holdings"][0]["pnl"] == 10000
        assert result["source"] == "TradingAgents local holdings"

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
    @pytest.mark.parametrize("payload, expected_error", [
        ({"status": "error", "message": "行情源无数据", "rows": []}, "行情源无数据"),
        ({"status": "success", "rows": [{"ts_code": "000001.SZ"}]}, "当前标的"),
        ({"status": "success", "as_of_date": "2026-09-24",
          "rows": [{"ts_code": "600519.SH", "raw_factors": {"pe_ttm": 12.0}}]}, "基准日"),
        ({"status": "partial", "as_of_date": "2026-09-25",
          "rows": [{"ts_code": "600519.SH", "raw_factors": {"pe_ttm": None}}]}, "原始因子"),
    ])
    async def test_factor_snapshot_rejects_remote_error_or_wrong_symbol(
        self, monkeypatch, payload, expected_error,
    ):
        from tradingagents.core.lightweight_tools import make_get_mcp_factor_snapshot

        client = MagicMock()
        client.get_factor_snapshot = AsyncMock(return_value=payload)
        monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client",
                            AsyncMock(return_value=client))
        result = await make_get_mcp_factor_snapshot({})(
            ts_code="600519.SH", trade_date="2026-09-25"
        )
        assert expected_error in result["error"]

    @pytest.mark.asyncio
    async def test_factor_snapshot_preserves_partial_coverage_warning(self, monkeypatch):
        from tradingagents.core.lightweight_tools import make_get_mcp_factor_snapshot

        client = MagicMock()
        client.get_factor_snapshot = AsyncMock(return_value={
            "status": "partial", "as_of_date": "2026-09-25",
            "rows": [{"ts_code": "600519.SH", "raw_factors": {"pe_ttm": 12.0}}],
            "warnings": ["flow_missing"],
        })
        monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client",
                            AsyncMock(return_value=client))
        result = await make_get_mcp_factor_snapshot({})(
            ts_code="600519.SH", trade_date="2026-09-25"
        )
        assert result["snapshot"]["rows"][0]["ts_code"] == "600519.SH"
        assert "覆盖不完整" in result["warnings"][-1]

    @pytest.mark.asyncio
    async def test_factor_snapshot_warns_on_missing_dimension_even_with_success(self, monkeypatch):
        from tradingagents.core.lightweight_tools import make_get_mcp_factor_snapshot

        client = MagicMock()
        client.get_factor_snapshot = AsyncMock(return_value={
            "status": "success", "as_of_date": "2026-09-25", "warnings": [],
            "rows": [{"ts_code": "600519.SH", "raw_factors": {"pe_ttm": 12.0},
                      "data_coverage": {"valuation": "available", "flow": "missing"},
                      "factor_scores": {"valuation": 50.0, "flow": 50.0}}],
        })
        monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client",
                            AsyncMock(return_value=client))
        result = await make_get_mcp_factor_snapshot({})(
            ts_code="600519.SH", trade_date="2026-09-25"
        )
        assert "flow" in result["warnings"][-1]
        assert "占位值" in result["warnings"][-1]
        assert result["snapshot"]["rows"][0]["factor_scores"]["flow"] == 50.0

    @pytest.mark.asyncio
    async def test_risk_announcement_dates_are_bounded_and_explain_zero_hits(self, monkeypatch):
        from tradingagents.core.lightweight_tools import make_get_mcp_risk_announcements

        client = MagicMock()
        client.get_risk_announcements = AsyncMock(return_value={
            "ts_code": "600519.SH", "rows": [], "warnings": [],
        })
        monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client",
                            AsyncMock(return_value=client))
        result = await make_get_mcp_risk_announcements({})(
            ts_code="600519.sh", end_date="2026-09-25"
        )
        assert result["as_of_date"] == "2026-09-25"
        assert result["count"] == 0
        assert "不代表没有" in " ".join(result["warnings"])
        client.get_risk_announcements.assert_awaited_once_with(
            "600519.SH", "20260627", "20260925",
            keywords=["立案", "问询", "违规", "处罚", "退市", "减持", "预亏"],
        )

    @pytest.mark.asyncio
    async def test_risk_announcement_limit_keeps_latest_dates(self, monkeypatch):
        from tradingagents.core.lightweight_tools import make_get_mcp_risk_announcements

        client = MagicMock()
        client.get_risk_announcements = AsyncMock(return_value={
            "ts_code": "600519.SH",
            "rows": [{"ann_date": f"202609{day:02d}"} for day in range(1, 22)],
        })
        monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client",
                            AsyncMock(return_value=client))
        result = await make_get_mcp_risk_announcements({})(
            ts_code="600519.SH", end_date="2026-09-25"
        )
        assert result["count"] == 21
        assert len(result["rows"]) == 20
        assert result["rows"][0]["ann_date"] == "2026-09-21"
        assert result["rows"][-1]["ann_date"] == "2026-09-02"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload, expected", [
        ({"ts_code": "000001.SZ", "rows": []}, "标的与请求不一致"),
        ({"rows": [{"ann_date": "20260926"}]}, "超出查询区间"),
        ({"rows": [{"ann_date": "bad"}]}, "日期格式无效"),
    ])
    async def test_risk_announcement_rejects_conflicting_evidence(
        self, monkeypatch, payload, expected,
    ):
        from tradingagents.core.lightweight_tools import make_get_mcp_risk_announcements

        client = MagicMock()
        client.get_risk_announcements = AsyncMock(return_value=payload)
        monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client",
                            AsyncMock(return_value=client))
        result = await make_get_mcp_risk_announcements({})(
            ts_code="600519.SH", end_date="2026-09-25"
        )
        assert expected in result["error"]

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
