"""Regression tests for reflection outcome fetching.

Covers two bugs that silently broke reflection (0 cases processed):
1. fetch_outcome treated MCP's {"rows": {ts_code: [records]}} dict as a flat
   row list, so isinstance(rows, list) was always False -> MCP skipped.
2. _compute_return_from_rows compared trade_date "YYYY-MM-DD" (with dashes)
   against signal_date_compact "YYYYMMDD"; '-' sorts before digits, so the
   signal row was never found even when MCP returned data.
"""

from __future__ import annotations

import asyncio

import pytest

from tradingagents.core.reflection import ReflectionEngine


@pytest.fixture()
def engine():
    return ReflectionEngine(db=None, config={})


@pytest.mark.unit
def test_compute_return_from_rows_handles_dashed_trade_date(engine):
    """MCP records use YYYY-MM-DD; the dash must not break comparison."""
    rows = [
        {"trade_date": "2026-07-02", "close": 9.5},
        {"trade_date": "2026-07-03", "close": 10.0},
        {"trade_date": "2026-07-10", "close": 11.0},
    ]
    result = engine._compute_return_from_rows(rows, "2026-07-03", 5)
    assert result is not None
    assert result["source"] == "mcp"
    assert result["close_at_signal"] == 10.0
    # 5 trading days after signal -> the 2026-07-10 row (index 2)
    assert result["close_at_horizon"] == 11.0


@pytest.mark.unit
def test_fetch_outcome_parses_mcp_rows_dict(engine, monkeypatch):
    """MCP returns {"rows": {ts_code: [records]}} (dict keyed by symbol);
    fetch_outcome must extract the symbol's list, not skip MCP."""

    class FakeClient:
        async def get_stock_daily(self, *, ts_codes, start_date, end_date, adj_type):
            return {
                "rows": {
                    "600549.SH": [
                        {"trade_date": "2026-07-03", "close": 10.0},
                        {"trade_date": "2026-07-10", "close": 11.0},
                    ]
                }
            }

    async def fake_get_mcp_client(config):
        return FakeClient()

    import tradingagents.core.mcp_client as mcp_client

    monkeypatch.setattr(mcp_client, "get_mcp_client", fake_get_mcp_client)

    result = asyncio.run(engine.fetch_outcome("600549.SH", "2026-07-03", 5))
    assert result is not None
    assert result["source"] == "mcp"
    assert result["close_at_signal"] == 10.0
    assert result["close_at_horizon"] == 11.0
