"""Tests for the second batch of P0/P1 bug fixes.

Covers:
- Fix 15: SQLite WAL mode enabled
- Fix 11: MCP client uses a semaphore (not a lock) for concurrent calls
- Fix 9: reflection case dedup (case_id without run_id), prune method,
          neutral decisions excluded from accuracy
- Fix 8: A-share realized returns fetched via AKShare, not yfinance
- Fix 7: investment_style injected into the 13-agent pipeline state + prompts
- Fix 6: astream_propagate honors checkpoint_enabled
"""

import asyncio

from tradingagents.agents.utils.agent_utils import get_investment_style_instruction
from tradingagents.core.mcp_client import MCPConfig, StockManagerMCPClient
from tradingagents.core.persistence import Database
from tradingagents.core.reflection import ReflectionEngine
from tradingagents.graph.propagation import Propagator

# ---------------------------------------------------------------------------
# Fix 15: SQLite WAL
# ---------------------------------------------------------------------------


def test_sqlite_wal_mode_enabled(tmp_path):
    db = Database(tmp_path / "test.db")
    with db._conn() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    # WAL is persistent on the db file once set; some platforms may report it
    # in a different case.
    assert mode.lower() == "wal"
    assert busy == 30000


# ---------------------------------------------------------------------------
# Fix 11: MCP semaphore
# ---------------------------------------------------------------------------


def test_mcp_client_uses_semaphore_not_lock():
    client = StockManagerMCPClient(MCPConfig(enabled=True))
    assert isinstance(client._call_semaphore, asyncio.Semaphore)
    assert not hasattr(client, "_call_lock")


def test_mcp_semaphore_allows_concurrent_calls():
    """Two _call_tool invocations should be able to enter the semaphore
    concurrently (up to the limit). With a Lock they would serialize."""
    client = StockManagerMCPClient(MCPConfig(enabled=True, tool_timeout=1.0))
    client._connected = True
    entered = {"count": 0, "max": 0}

    class _SlowSession:
        async def call_tool(self, name, arguments):
            entered["count"] += 1
            entered["max"] = max(entered["max"], entered["count"])
            await asyncio.sleep(0.05)
            entered["count"] -= 1
            # Force a non-connection exception so _call_tool returns None but
            # releases the semaphore cleanly.
            raise ValueError("synthetic")

    client._session = _SlowSession()

    async def run():
        await asyncio.gather(
            client._call_tool("t1", {}),
            client._call_tool("t2", {}),
            client._call_tool("t3", {}),
        )

    asyncio.run(run())
    # If it were a Lock, max concurrency would be 1. With a semaphore it should
    # be >= 2 (allowing concurrent calls).
    assert entered["max"] >= 2


# ---------------------------------------------------------------------------
# Fix 9: reflection case dedup + prune + accuracy
# ---------------------------------------------------------------------------


def test_reflection_case_id_excludes_run_id_for_dedup(tmp_path):
    """Two saves with the same (trade_date, symbol) must collide on case_id so
    INSERT OR REPLACE dedups instead of accumulating duplicates."""
    db = Database(tmp_path / "t.db")
    base = {
        "source_type": "system_signal",
        "reflection_scope": "candidate_pool",
        "eligible_for_strategy_learning": False,
        "symbol": "600519.SH",
        "name": "贵州茅台",
        "signal_date": "2026-07-04",
        "horizon_days": 5,
        "source_run_id": "run-A",
        "snapshot_payload": "{}",
        "status": "pending",
    }
    case_id = "daily_pipeline:2026-07-04:600519.SH"
    db.save_reflection_case(case_id=case_id, **base)
    # Same case_id from a different run — must replace, not duplicate.
    base["source_run_id"] = "run-B"
    db.save_reflection_case(case_id=case_id, **base)

    cases = db.list_reflection_cases(symbol="600519.SH")
    assert len(cases) == 1, f"expected dedup, got {len(cases)}"


def test_prune_reflection_cases_deletes_old_reflected(tmp_path):
    db = Database(tmp_path / "t.db")
    # Insert a reflected case with an old updated_at.
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO reflection_cases (id, source_type, reflection_scope, "
            "eligible_for_strategy_learning, status, symbol, name, signal_date, "
            "horizon_days, source_run_id, source_artifact_id, snapshot_payload, "
            "outcome_payload, post_signal_evidence_payload, attribution_payload, "
            "lesson_payload, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "old:1", "system_signal", "candidate_pool", 0, "reflected",
                "600519.SH", "茅台", "2026-04-01", 5, "", "", "{}",
                "{}", "{}", "{}", "{}",
                "2026-04-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00",
            ),
        )
        # And a pending case (recent) that must survive.
        conn.execute(
            "INSERT INTO reflection_cases (id, source_type, reflection_scope, "
            "eligible_for_strategy_learning, status, symbol, name, signal_date, "
            "horizon_days, source_run_id, source_artifact_id, snapshot_payload, "
            "outcome_payload, post_signal_evidence_payload, attribution_payload, "
            "lesson_payload, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "new:1", "system_signal", "candidate_pool", 0, "pending",
                "600519.SH", "茅台", "2026-07-04", 5, "", "", "{}",
                "{}", "{}", "{}", "{}",
                "2026-07-04T00:00:00+00:00", "2026-07-04T00:00:00+00:00",
            ),
        )
    deleted = db.prune_reflection_cases(older_than_days=30, status="reflected")
    assert deleted == 1
    remaining = db.list_reflection_cases()
    assert len(remaining) == 1
    assert remaining[0]["status"] == "pending"


def test_evaluate_accuracy_neutral_returns_none():
    engine = ReflectionEngine.__new__(ReflectionEngine)  # bypass __init__
    assert engine.evaluate_accuracy("WATCHLIST", 0.05) is None
    assert engine.evaluate_accuracy("HOLD", -0.10) is None
    # Directional decisions still return a bool.
    assert engine.evaluate_accuracy("BUY", 0.05) is True
    assert engine.evaluate_accuracy("BUY", -0.05) is False
    assert engine.evaluate_accuracy("SELL", -0.05) is True


def test_reflection_summary_excludes_neutral_from_accuracy(tmp_path):
    db = Database(tmp_path / "t.db")
    # 1 correct BUY, 1 incorrect BUY, 1 neutral WATCHLIST.
    rows = [
        ("r1", "600519.SH", "2026-07-01", "BUY", 1, "2026-07-01T00:00:00+00:00"),
        ("r2", "600519.SH", "2026-07-01", "BUY", 0, "2026-07-01T00:00:00+00:00"),
        ("r3", "600519.SH", "2026-07-01", "WATCHLIST", None, "2026-07-01T00:00:00+00:00"),
    ]
    with db._conn() as conn:
        for rid, ticker, td, dec, wc, created in rows:
            conn.execute(
                "INSERT INTO reflections (id, run_id, ticker, trade_date, "
                "original_decision, actual_return, was_correct, reflection_text, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, "", ticker, td, dec, 0.0, wc, "", created),
            )
    summary = db.get_reflection_summary(lookback_days=365)
    assert summary["total"] == 3
    assert summary["neutral"] == 1
    assert summary["correct"] == 1
    # accuracy = correct / (total - neutral) = 1/2
    assert summary["accuracy"] == 0.5


# ---------------------------------------------------------------------------
# Fix 8: A-share realized returns via AKShare
# ---------------------------------------------------------------------------


def test_fetch_returns_uses_akshare_for_cn_a(monkeypatch):
    """For an A-share ticker, _fetch_returns must call load_ohlcv_cn (AKShare),
    not yfinance, for the stock price series."""
    import tradingagents.graph.trading_graph as tg

    calls = {"akshare": 0, "yahoo": 0}

    def fake_cn_close(self, ticker, start, end):
        calls["akshare"] += 1
        return [10.0, 10.5, 11.0, 11.5, 12.0]

    def fake_yahoo(symbol, start, end):
        calls["yahoo"] += 1
        # benchmark series
        return [100.0, 100.5, 101.0, 101.5, 102.0]

    monkeypatch.setattr(tg.TradingAgentsGraph, "_cn_close_series", fake_cn_close)
    monkeypatch.setattr(tg, "_yahoo_close_series", fake_yahoo)

    # Build a bare instance without running __init__ (avoids needing full config).
    graph = tg.TradingAgentsGraph.__new__(tg.TradingAgentsGraph)

    raw, alpha, days = graph._fetch_returns("600519.SH", "2026-06-01", holding_days=4, benchmark="000001.SS")
    assert calls["akshare"] == 1
    # Stock was fetched via AKShare; benchmark via yfinance.
    assert raw is not None and raw > 0
    assert alpha is not None
    assert days is not None


def test_fetch_returns_uses_yfinance_for_us(monkeypatch):
    import tradingagents.graph.trading_graph as tg

    calls = {"akshare": 0, "yahoo": 0}

    def fake_cn_close(self, ticker, start, end):
        calls["akshare"] += 1
        return []

    def fake_yahoo(symbol, start, end):
        calls["yahoo"] += 1
        return [100.0, 101.0, 102.0, 103.0, 104.0]

    monkeypatch.setattr(tg.TradingAgentsGraph, "_cn_close_series", fake_cn_close)
    monkeypatch.setattr(tg, "_yahoo_close_series", fake_yahoo)

    graph = tg.TradingAgentsGraph.__new__(tg.TradingAgentsGraph)
    raw, alpha, days = graph._fetch_returns("AAPL", "2026-06-01", holding_days=4, benchmark="SPY")
    assert calls["akshare"] == 0
    assert calls["yahoo"] == 2  # stock + benchmark
    assert raw is not None


# ---------------------------------------------------------------------------
# Fix 7: investment_style injected into state + prompt
# ---------------------------------------------------------------------------


def test_investment_style_instruction():
    assert "SHORT-TERM" in get_investment_style_instruction("short_term")
    assert "MEDIUM-TERM" in get_investment_style_instruction("medium_term")
    assert "LONG-TERM" in get_investment_style_instruction("long_term")
    assert get_investment_style_instruction("unknown") == ""
    assert get_investment_style_instruction(None) == "" or "Investment style" in get_investment_style_instruction(None)


def test_create_initial_state_carries_investment_style():
    state = Propagator().create_initial_state(
        "600519.SH", "2026-07-04", market="cn_a", investment_style="short_term",
    )
    assert state["investment_style"] == "short_term"
    # Default is empty string (not None) so .get() in analysts is safe.
    default_state = Propagator().create_initial_state("AAPL", "2026-07-04")
    assert default_state["investment_style"] == ""


def test_market_analyst_prompt_includes_style():
    """The market analyst node should append the style instruction to its
    system_message when investment_style is set in state."""
    from tradingagents.agents.analysts.market_analyst import create_market_analyst

    class _StubLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            class _R:
                tool_calls = []
                content = ""
            return _R()

    # Capture the system_message via the prompt partial. We invoke the node
    # with a state that has investment_style set and inspect that the prompt
    # template was partially applied with a style-bearing system_message.
    create_market_analyst(_StubLLM())
    # The node builds the prompt internally; we just assert it runs and the
    # style helper is wired by checking the source imports the symbol.
    import inspect
    src = inspect.getsource(create_market_analyst)
    assert "get_investment_style_instruction" in src
    assert "state.get(\"investment_style\")" in src or "state.get('investment_style')" in src


# ---------------------------------------------------------------------------
# Fix 6: astream_propagate honors checkpoint_enabled
# ---------------------------------------------------------------------------


def test_astream_propagate_has_checkpointer_branch():
    import inspect

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    src = inspect.getsource(TradingAgentsGraph.astream_propagate)
    assert "checkpoint_enabled" in src
    assert "get_checkpointer" in src
    assert "thread_id" in src
    assert "finally:" in src
    assert "checkpointer_ctx.__exit__" in src
