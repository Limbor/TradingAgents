"""Real-time data enrichment for quant-ranked candidates.

Fetches news, announcements, northbound flow, and risk events in parallel
for each candidate to provide LLM reviewers with actionable market context.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Timeout for individual data fetch operations
_FETCH_TIMEOUT = 8.0
# Max headlines/announcements to include per candidate
_MAX_NEWS_ITEMS = 3
_MAX_ANNOUNCEMENT_ITEMS = 3


@dataclass
class CandidateContext:
    """Enrichment context for one candidate symbol."""

    news_summary: str = ""
    announcement_summary: str = ""
    northbound_signal: str = ""
    risk_events: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.news_summary or self.announcement_summary or self.northbound_signal or self.risk_events)

    def to_prompt_section(self) -> str:
        """Render as a prompt section for LLM injection."""
        parts = ["## 近期市场信息（仅供参考，不保证完整性）"]

        parts.append("\n### 新闻动态")
        parts.append(self.news_summary or "无近期相关新闻")

        parts.append("\n### 重要公告")
        parts.append(self.announcement_summary or "无近期公告")

        parts.append("\n### 资金流向")
        parts.append(self.northbound_signal or "数据暂不可用")

        if self.risk_events:
            parts.append("\n### 风险事件")
            parts.append("；".join(self.risk_events))

        return "\n".join(parts)


async def enrich_candidates(
    candidates: list[dict[str, Any]],
    trade_date: str,
    config: dict[str, Any],
) -> dict[str, CandidateContext]:
    """Concurrently fetch real-time context for each candidate.

    Returns a mapping of symbol → CandidateContext.
    Failures are silently degraded (empty context) to avoid blocking the pipeline.
    """
    if not candidates:
        return {}

    # Parse trade_date for date arithmetic
    try:
        td = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        td = datetime.now().date()

    news_start = (td - timedelta(days=3)).isoformat()
    announcement_start = (td - timedelta(days=7)).isoformat()

    # Collect symbols
    symbols = [str(c.get("symbol") or c.get("ts_code") or "") for c in candidates]
    symbols = [s for s in symbols if s]

    # Get MCP client if available
    mcp_client = None
    try:
        from tradingagents.core.mcp_client import get_mcp_client
        mcp_client = await get_mcp_client(config)
    except Exception:
        pass

    # Fetch northbound flow once (shared across all candidates)
    northbound_text = await _fetch_northbound(trade_date, config)

    # Fetch per-candidate data concurrently
    tasks = [
        _enrich_one(
            symbol=sym,
            trade_date=trade_date,
            news_start=news_start,
            announcement_start=announcement_start,
            northbound_text=northbound_text,
            mcp_client=mcp_client,
            config=config,
        )
        for sym in symbols
    ]
    results = await asyncio.gather(*tasks)

    return {sym: ctx for sym, ctx in zip(symbols, results)}


async def _enrich_one(
    symbol: str,
    trade_date: str,
    news_start: str,
    announcement_start: str,
    northbound_text: str,
    mcp_client: Any | None,
    config: dict[str, Any],
) -> CandidateContext:
    """Fetch all enrichment data for a single symbol."""
    ctx = CandidateContext(northbound_signal=northbound_text)

    # Parallel fetch news + announcements + risk events
    news_task = _fetch_news(symbol, news_start, trade_date, config)
    announcement_task = _fetch_announcements(symbol, announcement_start, trade_date, config)
    risk_task = _fetch_risk_events(symbol, trade_date, mcp_client)

    results = await asyncio.gather(news_task, announcement_task, risk_task, return_exceptions=True)

    if not isinstance(results[0], BaseException):
        ctx.news_summary = results[0]
    if not isinstance(results[1], BaseException):
        ctx.announcement_summary = results[1]
    if not isinstance(results[2], BaseException):
        ctx.risk_events = results[2]

    return ctx


async def _fetch_news(symbol: str, start_date: str, end_date: str, config: dict[str, Any]) -> str:
    """Fetch recent news headlines for a symbol."""
    try:
        from tradingagents.dataflows.akshare_news import get_news

        raw = await asyncio.wait_for(
            asyncio.to_thread(get_news, symbol, start_date, end_date),
            timeout=_FETCH_TIMEOUT,
        )
        if not raw or raw.startswith("Error") or raw.startswith("No "):
            return ""
        # Truncate to reasonable size for prompt injection
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        return "\n".join(lines[:_MAX_NEWS_ITEMS * 3])  # ~3 lines per news item
    except Exception as exc:
        logger.debug("News fetch failed for %s: %s", symbol, exc)
        return ""


async def _fetch_announcements(symbol: str, start_date: str, end_date: str, config: dict[str, Any]) -> str:
    """Fetch recent announcements for a symbol."""
    try:
        from tradingagents.dataflows.akshare_announcements import get_announcements

        raw = await asyncio.wait_for(
            asyncio.to_thread(get_announcements, symbol, start_date, end_date),
            timeout=_FETCH_TIMEOUT,
        )
        if not raw or raw.startswith("Error") or raw.startswith("No "):
            return ""
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        return "\n".join(lines[:_MAX_ANNOUNCEMENT_ITEMS * 3])
    except Exception as exc:
        logger.debug("Announcements fetch failed for %s: %s", symbol, exc)
        return ""


async def _fetch_northbound(trade_date: str, config: dict[str, Any]) -> str:
    """Fetch northbound (Stock Connect) flow summary."""
    try:
        from tradingagents.dataflows.akshare_cn_specific import get_northbound_flow

        raw = await asyncio.wait_for(
            asyncio.to_thread(get_northbound_flow, trade_date, 5),
            timeout=_FETCH_TIMEOUT,
        )
        if not raw or raw.startswith("Error"):
            return ""
        # Extract the summary portion (first few lines)
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        return "\n".join(lines[:8])
    except Exception as exc:
        logger.debug("Northbound flow fetch failed: %s", exc)
        return ""


async def _fetch_risk_events(symbol: str, trade_date: str, mcp_client: Any | None) -> list[str]:
    """Fetch risk announcements from MCP if available."""
    if mcp_client is None:
        return []
    try:
        td = datetime.strptime(trade_date, "%Y-%m-%d").date()
        start = (td - timedelta(days=30)).strftime("%Y%m%d")
        end = td.strftime("%Y%m%d")
        # Convert symbol format: 600519.SH → 600519.SH (MCP expects ts_code)
        ts_code = symbol.upper()

        result = await asyncio.wait_for(
            mcp_client.get_risk_announcements(
                ts_code=ts_code,
                start_date=start,
                end_date=end,
                keywords=["退市", "违规", "处罚", "预亏", "减持", "ST"],
            ),
            timeout=_FETCH_TIMEOUT,
        )
        if not result or not isinstance(result, dict):
            return []
        rows = result.get("rows") or result.get("data", {}).get("rows") or []
        events = []
        for row in rows[:5]:
            if isinstance(row, dict):
                title = row.get("title") or row.get("ann_title") or ""
                if title:
                    events.append(str(title)[:80])
        return events
    except Exception as exc:
        logger.debug("Risk events fetch failed for %s: %s", symbol, exc)
        return []
