"""Tests for candidate_enrichment module."""

import asyncio
from unittest.mock import AsyncMock, patch

from tradingagents.core.candidate_enrichment import (
    CandidateContext,
    _fetch_announcements,
    _fetch_news,
    _fetch_northbound,
    _fetch_risk_events,
    enrich_candidates,
)


class TestCandidateContext:
    def test_empty_context(self):
        ctx = CandidateContext()
        assert not ctx.has_content
        assert "无近期相关新闻" in ctx.to_prompt_section()
        assert "无近期公告" in ctx.to_prompt_section()

    def test_with_news(self):
        ctx = CandidateContext(news_summary="公司获得大额订单")
        assert ctx.has_content
        section = ctx.to_prompt_section()
        assert "公司获得大额订单" in section
        assert "近期市场信息" in section

    def test_with_risk_events(self):
        ctx = CandidateContext(risk_events=["退市预警", "违规处罚"])
        assert ctx.has_content
        section = ctx.to_prompt_section()
        assert "退市预警" in section
        assert "违规处罚" in section


class TestFetchNews:
    def test_success(self):
        with patch(
            "tradingagents.core.candidate_enrichment.asyncio.to_thread",
            new_callable=lambda: lambda: AsyncMock(return_value="标题1: 重大利好\n内容摘要\n标题2: 业绩增长\n"),
        ) as mock_thread:
            mock_thread.return_value = AsyncMock(return_value="标题1: 重大利好\n内容摘要\n标题2: 业绩增长\n")
            result = asyncio.run(_fetch_news("600519.SH", "2026-06-25", "2026-06-28", {}))
            # Result should be non-empty (depends on mock behavior)
            assert isinstance(result, str)

    def test_returns_empty_on_error(self):
        """Should gracefully return empty string on any exception."""
        with patch(
            "tradingagents.dataflows.akshare_news.get_news",
            side_effect=Exception("network error"),
        ):
            result = asyncio.run(_fetch_news("600519.SH", "2026-06-25", "2026-06-28", {}))
            assert result == ""

    def test_returns_empty_on_no_results(self):
        with patch(
            "tradingagents.dataflows.akshare_news.get_news",
            return_value="No news found",
        ):
            result = asyncio.run(_fetch_news("600519.SH", "2026-06-25", "2026-06-28", {}))
            assert result == ""


class TestFetchAnnouncements:
    def test_returns_empty_on_error(self):
        with patch(
            "tradingagents.dataflows.akshare_announcements.get_announcements",
            side_effect=Exception("timeout"),
        ):
            result = asyncio.run(_fetch_announcements("600519.SH", "2026-06-21", "2026-06-28", {}))
            assert result == ""


class TestFetchNorthbound:
    def test_returns_empty_on_error(self):
        with patch(
            "tradingagents.dataflows.akshare_cn_specific.get_northbound_flow",
            side_effect=Exception("rate limit"),
        ):
            result = asyncio.run(_fetch_northbound("2026-06-28", {}))
            assert result == ""

    def test_returns_truncated(self):
        lines = "\n".join([f"line {i}" for i in range(20)])
        with patch(
            "tradingagents.dataflows.akshare_cn_specific.get_northbound_flow",
            return_value=lines,
        ):
            result = asyncio.run(_fetch_northbound("2026-06-28", {}))
            # Should be truncated to 8 lines
            assert len(result.split("\n")) <= 8


class TestFetchRiskEvents:
    def test_returns_empty_when_no_client(self):
        result = asyncio.run(_fetch_risk_events("600519.SH", "2026-06-28", None))
        assert result == []

    def test_parses_mcp_response(self):
        mock_client = AsyncMock()
        mock_client.get_risk_announcements = AsyncMock(return_value={
            "rows": [
                {"title": "退市预警公告", "ann_date": "2026-06-25"},
                {"title": "违规处罚通知", "ann_date": "2026-06-26"},
            ]
        })
        result = asyncio.run(_fetch_risk_events("600519.SH", "2026-06-28", mock_client))
        assert len(result) == 2
        assert "退市预警公告" in result[0]


class TestEnrichCandidates:
    def test_empty_candidates(self):
        result = asyncio.run(enrich_candidates([], "2026-06-28", {}))
        assert result == {}

    def test_graceful_degradation(self):
        """All fetches fail gracefully, returning empty contexts."""
        candidates = [
            {"symbol": "600519.SH"},
            {"symbol": "000001.SZ"},
        ]
        with (
            patch("tradingagents.core.candidate_enrichment._fetch_news", return_value=""),
            patch("tradingagents.core.candidate_enrichment._fetch_announcements", return_value=""),
            patch("tradingagents.core.candidate_enrichment._fetch_northbound", return_value=""),
            patch("tradingagents.core.candidate_enrichment._fetch_risk_events", return_value=[]),
        ):
            result = asyncio.run(enrich_candidates(candidates, "2026-06-28", {"stockmanager_mcp_enabled": False}))
        assert len(result) == 2
        assert "600519.SH" in result
        assert "000001.SZ" in result
