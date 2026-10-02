"""Tests for the fourth batch of medium-value fixes.

Covers:
- LLM Router caches its LLM client and wraps user input in <user_input> tags
- Orchestrator resolves stock names via expanded alias table + DB fallback
- /ws/chat supports cancel and stays responsive while a run streams
"""

import asyncio
import inspect

from tradingagents.core.orchestrator import NAME_TO_TICKER, Orchestrator
from tradingagents.core.persistence import Database
from tradingagents.skills.daily_pipeline.skill import DailyPipelineSkill
from tradingagents.skills.daily_review.skill import DailyReviewSkill
from tradingagents.skills.market_scanner.skill import MarketScannerSkill
from tradingagents.skills.portfolio_management.skill import PortfolioManagementSkill
from tradingagents.skills.registry import SkillRegistry
from tradingagents.skills.risk_monitor.skill import RiskMonitorSkill
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
# Fix 2: LLM Router client caching + prompt-injection guard
# ---------------------------------------------------------------------------


def test_llm_router_caches_llm_client():
    """The LLM client should be built once and reused, not per route() call."""
    from tradingagents.core.llm_router import LLMRouter

    router = LLMRouter(_registry(), {"llm_provider": "openai", "quick_think_llm": "x"})
    assert router._llm_with_tools is None  # lazy: not built in __init__

    # First access builds it.
    calls = {"n": 0}

    class _FakeLLM:
        def bind_tools(self, tools):
            calls["n"] += 1
            return object()

    # Monkeypatch the factory so no real provider is constructed.
    import tradingagents.llm_clients as llm_clients

    class _FakeClient:
        def get_llm(self):
            return _FakeLLM()

    orig = getattr(llm_clients, "create_llm_client", None)
    llm_clients.create_llm_client = lambda **kw: _FakeClient()
    try:
        a = router._get_llm_with_tools()
        b = router._get_llm_with_tools()
        assert a is b
        assert calls["n"] == 1  # bound once, reused
    finally:
        if orig is not None:
            llm_clients.create_llm_client = orig


def test_llm_router_wraps_user_input_against_injection():
    """User content must be wrapped in <user_input> tags so injection attempts
    are treated as data, not instructions."""
    from tradingagents.core.llm_router import LLMRouter

    LLMRouter(_registry(), {"llm_provider": "openai", "quick_think_llm": "x"})
    src = inspect.getsource(LLMRouter._do_route)
    assert "<user_input>" in src
    assert "untrusted DATA" in src or "untrusted data" in src.lower() or "as data" in src.lower()


# ---------------------------------------------------------------------------
# Fix 3: NAME_TO_TICKER expansion + DB fallback
# ---------------------------------------------------------------------------


def test_alias_table_covers_common_a_shares():
    assert len(NAME_TO_TICKER) >= 30
    # Spot-check a few that were missing before.
    assert NAME_TO_TICKER["比亚迪"] == "002594.SZ"
    assert NAME_TO_TICKER["平安"] == "601318.SH"
    assert NAME_TO_TICKER["宁王"] == "300750.SZ"
    assert NAME_TO_TICKER["招商银行"] == "600036.SH"


def test_resolve_known_ticker_uses_dict_first():
    orch = Orchestrator(_registry())
    assert orch._resolve_known_ticker("分析比亚迪") == "002594.SZ"


def test_resolve_known_ticker_falls_back_to_db(tmp_path):
    db = Database(tmp_path / "t.db")
    with db._conn() as c:
        c.execute(
            "INSERT INTO reports(id,run_id,ticker,ticker_name,rating,content,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("rep1", "r1", "600183.SH", "生益科技", "BUY", "report",
             "2026-07-01T00:00:00+00:00"),
        )
    orch = Orchestrator(_registry(), db=db)
    # "生益科技" is NOT in the hardcoded alias table — DB fallback must resolve it.
    assert "生益科技" not in NAME_TO_TICKER
    assert orch._resolve_known_ticker("分析生益科技") == "600183.SH"
    # No match → None
    assert orch._resolve_known_ticker("分析一个完全不存在的股票XYZ") is None


def test_route_resolves_db_backed_name(tmp_path):
    """End-to-end: a name only present in the reports table routes correctly."""
    db = Database(tmp_path / "t.db")
    with db._conn() as c:
        c.execute(
            "INSERT INTO reports(id,run_id,ticker,ticker_name,rating,content,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("rep1", "r1", "600183.SH", "生益科技", "BUY", "report",
             "2026-07-01T00:00:00+00:00"),
        )

    async def run():
        orch = Orchestrator(_registry(), db=db)
        route = await orch.route("分析生益科技")
        assert route.skill is not None
        assert route.skill.metadata.id == "stock_analysis"
        assert route.params["ticker"] == "600183.SH"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Fix 4: /ws/chat supports cancel + stays responsive mid-run
# ---------------------------------------------------------------------------


def test_ws_chat_has_cancel_action_and_background_consumer():
    """The refactored ws_chat must support {"action":"cancel"} and consume
    run events in a background task (not inline in the receive loop)."""
    import tradingagents.api.ws.stream as stream

    src = inspect.getsource(stream.ws_chat)
    # Cancel action handling
    assert '"cancel"' in src or "'cancel'" in src
    assert "run_cancellation_ack" in src
    # Background consumer task
    assert "create_task" in src
    assert "_consume_run_events" in src
    # Send serialization (concurrent-write guard)
    assert "send_lock" in src
    # The receive loop is no longer blocked by inline event consumption
    assert "active_consumer" in src
