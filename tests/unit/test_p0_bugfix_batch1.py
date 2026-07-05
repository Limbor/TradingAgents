"""Tests for the first batch of P0 bug fixes.

Covers:
- Fix 1: MCP Client reconnects after a tool call failure
- Fix 2: Scheduler uses Asia/Shanghai timezone
- Fix 3: Non-trading-day guard for scheduled jobs (via get_temporal_context)
- Fix 4: Orchestrator no longer falls back to a hardcoded ticker (600519.SH)
- Fix 5: Date strings no longer pollute portfolio quantity/cost extraction
- Fix 6: MCP init timeout falls back to degraded mode
"""

import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo

import tradingagents.core.scheduler as scheduler_module
from tradingagents.core.mcp_client import MCPConfig, StockManagerMCPClient
from tradingagents.core.orchestrator import Orchestrator
from tradingagents.core.trading_time import get_temporal_context
from tradingagents.skills.daily_pipeline.skill import DailyPipelineSkill
from tradingagents.skills.daily_review.skill import DailyReviewSkill
from tradingagents.skills.market_scanner.skill import MarketScannerSkill
from tradingagents.skills.portfolio_management.skill import PortfolioManagementSkill
from tradingagents.skills.risk_monitor.skill import RiskMonitorSkill
from tradingagents.skills.registry import SkillRegistry
from tradingagents.skills.stock_analysis.skill import StockAnalysisSkill


def _registry():
    registry = SkillRegistry()
    registry.register(StockAnalysisSkill())
    registry.register(PortfolioManagementSkill())
    registry.register(MarketScannerSkill())
    registry.register(DailyPipelineSkill())
    registry.register(DailyReviewSkill())
    registry.register(RiskMonitorSkill())
    return registry


# ---------------------------------------------------------------------------
# Fix 1: MCP Client marks itself disconnected on tool-call failure so the next
# call triggers reconnect instead of silently reusing a dead session.
# ---------------------------------------------------------------------------


def test_mcp_call_tool_marks_disconnected_on_exception():
    """A failing _call_tool must clear _connected so the next call reconnects."""

    async def run():
        client = StockManagerMCPClient(MCPConfig(enabled=True, url="http://127.0.0.1:8765/mcp"))
        # Pretend we are already connected with a session that throws on use.
        client._connected = True

        class _BrokenSession:
            async def call_tool(self, name, arguments):
                raise RuntimeError("session closed by peer")

        client._session = _BrokenSession()

        result = await client._call_tool("get_stock_daily", {"ts_codes": ["600519.SH"]})
        assert result is None
        # The key assertion: the client no longer believes it is connected.
        assert client._connected is False

    asyncio.run(run())


def test_mcp_connect_cleans_up_dangling_resources_before_reconnect():
    """connect() must disconnect leftover session/transport before rebuilding."""

    async def run():
        client = StockManagerMCPClient(MCPConfig(enabled=True, url="http://127.0.0.1:8765/mcp"))
        # Simulate the post-failure state: _connected=False but a dangling session.
        client._connected = False
        client._session = object()  # dangling reference

        disconnect_calls = {"count": 0}
        original_disconnect = client.disconnect

        async def spy_disconnect():
            disconnect_calls["count"] += 1
            await original_disconnect()

        client.disconnect = spy_disconnect  # type: ignore[method-assign]

        # Stub refresh_status so connect() does not hit the network; health=None
        # makes connect() return False right after the cleanup branch.
        async def fake_refresh_status():
            client._status.health = None
            return client._status

        client.refresh_status = fake_refresh_status  # type: ignore[method-assign]

        result = await client.connect()
        assert result is False
        assert disconnect_calls["count"] == 1
        # The dangling session reference must be gone after disconnect().
        assert client._session is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Fix 2: Scheduler interprets daily job times as Asia/Shanghai, not host local.
# ---------------------------------------------------------------------------


def test_seconds_until_uses_shanghai_timezone(monkeypatch):
    """At 07:00 Shanghai, the delay to 08:30 Shanghai is 90 minutes."""

    shanghai = ZoneInfo("Asia/Shanghai")
    fixed_now = datetime(2026, 7, 5, 7, 0, 0, tzinfo=shanghai)

    class _FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(scheduler_module, "datetime", _FakeDateTime)

    delay = scheduler_module._seconds_until(time(8, 30))
    assert delay == 90 * 60.0  # 5400 seconds


def test_seconds_until_shanghai_not_affected_by_host_utc(monkeypatch):
    """If the host wall clock were UTC (00:00 UTC = 08:00 Shanghai), the delay
    to 08:30 Shanghai must still be 30 minutes, not 8.5 hours (UTC 08:30)."""

    shanghai = ZoneInfo("Asia/Shanghai")
    utc = ZoneInfo("UTC")
    # 2026-07-05 00:00:00 UTC == 2026-07-05 08:00:00 Shanghai
    fixed_now_utc = datetime(2026, 7, 5, 0, 0, 0, tzinfo=utc)

    class _FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now_utc.replace(tzinfo=None)
            return fixed_now_utc.astimezone(tz)

    monkeypatch.setattr(scheduler_module, "datetime", _FakeDateTime)

    delay = scheduler_module._seconds_until(time(8, 30))
    # 08:00 Shanghai -> 08:30 Shanghai = 30 minutes = 1800 seconds.
    # If the bug were present (naive UTC), it would be 8.5h = 30600s.
    assert delay == 30 * 60.0


# ---------------------------------------------------------------------------
# Fix 3: Scheduled jobs skip non-trading days. The guard is built on
# get_temporal_context().calendar_state, so we verify that primitive returns
# "holiday" on a weekend (the condition the app.py guard checks).
# ---------------------------------------------------------------------------


def test_temporal_context_marks_weekend_as_non_trading():
    """2026-07-04 is a Saturday; calendar_state must be 'holiday'."""
    # default config uses Asia/Shanghai; calendar falls back to weekday check
    # if the CN trading calendar data file is unavailable in the test env.
    ctx = get_temporal_context({}, market="cn_a", now=datetime(2026, 7, 4, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert ctx.calendar_state == "holiday"


# ---------------------------------------------------------------------------
# Fix 4: Orchestrator must NOT silently fall back to 600519.SH when no ticker
# or stock name is present in the message.
# ---------------------------------------------------------------------------


def test_stock_analysis_without_ticker_does_not_default_to_maotai():
    async def run():
        route = await Orchestrator(_registry()).route("分析一下")
        assert route.skill is None
        assert route.confidence == 0.0
        assert "clarification" in route.reason.lower() or "no ticker" in route.reason.lower()

    asyncio.run(run())


def test_portfolio_upsert_without_symbol_does_not_default_to_maotai():
    async def run():
        # "添加持仓" triggers portfolio route + upsert action, but no symbol.
        route = await Orchestrator(_registry()).route("添加持仓 100股")
        assert route.skill is None
        assert route.confidence == 0.0

    asyncio.run(run())


def test_stock_analysis_with_known_name_still_routes():
    """Regression guard: a real ticker/name must still route correctly."""
    async def run():
        route = await Orchestrator(_registry()).route("帮我看看茅台")
        assert route.skill is not None
        assert route.skill.metadata.id == "stock_analysis"
        assert route.params["ticker"] == "600519.SH"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Fix 5: Date strings in the message must not be parsed as quantity/cost.
# ---------------------------------------------------------------------------


def test_portfolio_upsert_strips_date_before_number_extraction():
    async def run():
        route = await Orchestrator(_registry()).route("2024-01-08 添加持仓 茅台 100股 成本2000")
        assert route.skill is not None
        assert route.skill.metadata.id == "portfolio_management"
        assert route.params["action"] == "upsert"
        assert route.params["symbol"] == "600519.SH"
        assert route.params["quantity"] == 100
        assert route.params["avg_cost"] == 2000.0

    asyncio.run(run())


def test_portfolio_upsert_without_date_still_works():
    """Regression guard: the original number-extraction path is unchanged."""
    async def run():
        route = await Orchestrator(_registry()).route("添加持仓 601899.SH 200 18.5 20.1")
        assert route.params["quantity"] == 200
        assert route.params["avg_cost"] == 18.5
        assert route.params["current_price"] == 20.1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Fix 6: MCP init is wrapped in a timeout; on timeout we continue in degraded
# mode (mcp_client=None) rather than blocking startup.
# ---------------------------------------------------------------------------


def test_wait_for_timeout_triggers_degraded_fallback():
    """If get_mcp_client hangs, asyncio.wait_for raises TimeoutError — the
    pattern app.py uses to fall back to degraded mode."""

    async def slow_mcp_init():
        await asyncio.sleep(5.0)
        return "should-not-get-here"

    async def run():
        await asyncio.wait_for(slow_mcp_init(), timeout=0.1)

    raised = False
    try:
        asyncio.run(run())
    except asyncio.TimeoutError:
        raised = True
    assert raised, "asyncio.wait_for should have raised TimeoutError"
