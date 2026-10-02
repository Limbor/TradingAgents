"""Regression tests for reflection outcome fetching.

Covers three bugs that silently broke reflection (0 cases processed):
1. fetch_outcome treated MCP's {"rows": {ts_code: [records]}} dict as a flat
   row list, so isinstance(rows, list) was always False -> MCP skipped.
2. _compute_return_from_rows compared trade_date "YYYY-MM-DD" (with dashes)
   against signal_date_compact "YYYYMMDD"; '-' sorts before digits, so the
   signal row was never found even when MCP returned data.
3. run_reflection_batch's pending fallback queried without due_only=True, so
   it ordered newest-first and stalled on not-yet-due cases (fetch_outcome
   returns None) instead of reaching the older, actually-due pending cases.

Also covers neutral-decision reflection: WATCHLIST/HOLD/MONITOR calls have no
directional right/wrong (excluded from the win-rate gate), but a large move vs
the benchmark is still learning material (missed_upside / validated_avoidance).
"""

from __future__ import annotations

import asyncio

import pytest

from tradingagents.core.reflection import ReflectionEngine, _neutral_attribution


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


class _FakeReflectionDB:
    """Minimal DB that honors due_only when listing pending cases.

    Mirrors persistence.list_reflection_cases ordering: due_only=True returns
    only elapsed cases oldest-first; due_only=False returns newest-first
    (which includes not-yet-due cases). Records every list call so the test
    can assert the pending fallback is queried with due_only=True.
    """

    def __init__(self, due_case, nondue_case):
        self._due = due_case
        self._nondue = nondue_case
        self.list_calls: list[dict] = []
        self.updated: list[tuple] = []

    def list_reflection_cases(self, *, status=None, limit=50, due_only=False, **kwargs):
        self.list_calls.append({"status": status, "due_only": due_only, "limit": limit})
        if status == "outcome_ready":
            return []
        if status == "pending":
            # newest-first (nondue) unless the caller restricts to due cases.
            return [self._due] if due_only else [self._nondue, self._due]
        return []

    def update_reflection_case(self, case_id, **fields):
        self.updated.append((case_id, fields.get("status")))

    def save_reflection(self, *args, **kwargs):
        pass

    def save_artifact(self, *args, **kwargs):
        pass


@pytest.mark.unit
def test_reflection_batch_reaches_due_pending_cases(engine, monkeypatch):
    """The pending fallback must use due_only=True so the batch processes the
    older, actually-due case instead of stalling on the newest not-yet-due one.
    """
    due_case = {
        "id": "due-1",
        "symbol": "600549.SH",
        "signal_date": "2026-07-03",
        "horizon_days": 5,
        "snapshot_payload": {"final_decision": "BUY"},
    }
    nondue_case = {
        "id": "nondue-1",
        "symbol": "000001.SZ",
        "signal_date": "2026-07-17",
        "horizon_days": 5,
        "snapshot_payload": {"final_decision": "BUY"},
    }
    db = _FakeReflectionDB(due_case, nondue_case)
    engine.db = db

    async def fake_fetch_outcome(symbol, signal_date, horizon_days):
        # Only the due case has an elapsed horizon with price data.
        if symbol == due_case["symbol"]:
            return {"actual_return": 0.05, "was_correct": True, "source": "mcp"}
        return None

    async def fake_evidence(symbol, signal_date, horizon_days):
        return {}

    async def fake_attribution(case, outcome, evidence):
        return {"attribution": "noise", "confidence": "low"}

    monkeypatch.setattr(engine, "fetch_outcome", fake_fetch_outcome)
    monkeypatch.setattr(engine, "fetch_post_signal_evidence", fake_evidence)
    monkeypatch.setattr(engine, "generate_attribution", fake_attribution)

    result = asyncio.run(engine.run_reflection_batch(max_per_run=1, horizon_days=5))

    # The pending fallback must have been queried with due_only=True.
    pending_calls = [c for c in db.list_calls if c["status"] == "pending"]
    assert pending_calls and all(c["due_only"] for c in pending_calls)
    # The due case (not the newest not-due one) was processed to reflected.
    assert result["processed"] == 1
    assert ("due-1", "reflected") in db.updated
    assert all(cid != "nondue-1" for cid, _ in db.updated)


def _neutral_case():
    return {
        "id": "neutral-1",
        "symbol": "600549.SH",
        "eligible_for_strategy_learning": False,  # WATCHLIST is candidate-pool
        "snapshot_payload": {
            "final_decision": "WATCHLIST",
            "gate_reasons": ["quant_score<70"],
            "risk_flags": ["data_coverage.flow=missing"],
        },
    }


@pytest.mark.unit
def test_neutral_attribution_missed_upside_uses_excess():
    """Neutral call + strong positive excess -> missed_upside, medium confidence."""
    outcome = {"was_correct": None, "actual_return": 0.09, "excess_return": 0.07, "horizon_days": 5}
    result = _neutral_attribution(_neutral_case(), outcome, {})
    assert result["attribution"] == "missed_upside"
    assert result["confidence"] == "medium"
    assert result["basis"] == "excess_over_benchmark"
    assert result["strategy_lesson"]


@pytest.mark.unit
def test_neutral_attribution_validated_avoidance_uses_excess():
    """Neutral call + strong negative excess -> validated_avoidance."""
    outcome = {"was_correct": None, "actual_return": -0.08, "excess_return": -0.06, "horizon_days": 5}
    result = _neutral_attribution(_neutral_case(), outcome, {})
    assert result["attribution"] == "validated_avoidance"
    assert result["confidence"] == "medium"
    assert result["risk_monitor_lesson"]


@pytest.mark.unit
def test_neutral_attribution_small_move_inconclusive():
    """Neutral call with a small move produces no actionable lesson."""
    outcome = {"was_correct": None, "actual_return": 0.01, "excess_return": 0.01, "horizon_days": 5}
    result = _neutral_attribution(_neutral_case(), outcome, {})
    assert result["attribution"] == "inconclusive"
    assert result["strategy_lesson"] == ""


@pytest.mark.unit
def test_neutral_attribution_absolute_fallback_is_low_confidence():
    """Without benchmark excess, absolute return is used but only low confidence."""
    outcome = {"was_correct": None, "actual_return": 0.09, "horizon_days": 5}
    result = _neutral_attribution(_neutral_case(), outcome, {})
    assert result["attribution"] == "missed_upside"
    assert result["confidence"] == "low"
    assert result["basis"] == "absolute_return"


class _FakeLessonDB:
    def __init__(self):
        self.saved: list[dict] = []

    def save_strategy_lesson(self, **kwargs):
        self.saved.append(kwargs)


@pytest.mark.unit
def test_neutral_lesson_created_despite_ineligible_case(engine):
    """Neutral lessons calibrate filters, so they persist even when the case is
    not eligible_for_strategy_learning (candidate-pool), unlike ex_ante_miss."""
    db = _FakeLessonDB()
    engine.db = db
    attribution = {
        "attribution": "missed_upside",
        "confidence": "medium",
        "strategy_lesson": "过滤过严",
        "suggested_adjustment": "放宽阈值",
        "missed_evidence": ["quant_score<70"],
    }
    lesson = engine.maybe_create_strategy_lesson(_neutral_case(), attribution)
    assert lesson
    assert lesson["lesson_type"] == "opportunity_cost"
    assert db.saved and db.saved[0]["active"] is False


@pytest.mark.unit
def test_neutral_lesson_skipped_when_low_confidence(engine):
    """Low-confidence neutral attributions (no benchmark) must not create a lesson."""
    db = _FakeLessonDB()
    engine.db = db
    attribution = {"attribution": "missed_upside", "confidence": "low"}
    assert engine.maybe_create_strategy_lesson(_neutral_case(), attribution) == {}
    assert db.saved == []


@pytest.mark.unit
@pytest.mark.parametrize("decision", ["WATCHLIST", "HOLD", "MONITOR"])
def test_evaluate_accuracy_neutral_excluded_from_gate(engine, decision):
    """Neutral decisions stay None so the directional win-rate gate is unaffected."""
    assert engine.evaluate_accuracy(decision, 0.12) is None
    assert engine.evaluate_accuracy(decision, -0.12) is None


class _FakeIndexClient:
    """MCP client stub returning a fixed index series for get_index_daily."""

    def __init__(self, ret: float = 0.10):
        self.calls: list[str] = []
        self._ret = ret

    async def get_index_daily(self, index_code, start_date, end_date):
        self.calls.append(index_code)
        base = 100.0
        return {
            "source": "mcp_index",
            "rows": [
                {"trade_date": start_date, "close": base},
                {"trade_date": end_date, "close": base * (1 + self._ret)},
            ],
        }


@pytest.mark.unit
def test_fetch_industry_return_maps_group_to_index(engine, monkeypatch):
    """A raw industry normalizes to a coarse group and resolves to its index."""
    client = _FakeIndexClient(ret=0.08)

    async def fake_get_mcp_client(config):
        return client

    import tradingagents.core.mcp_client as mcp_client

    monkeypatch.setattr(mcp_client, "get_mcp_client", fake_get_mcp_client)
    # 房地产开发 -> coarse group 地产 -> 801180.SI
    result = asyncio.run(engine.fetch_industry_return("房地产开发", "2026-07-03", 1))
    assert result is not None
    assert result["industry_group"] == "地产"
    assert result["industry_index_symbol"] == "801180.SI"
    assert client.calls == ["801180.SI"]
    assert result["actual_return"] == pytest.approx(0.08, abs=1e-6)


@pytest.mark.unit
def test_fetch_industry_return_none_for_unmapped_industry(engine, monkeypatch):
    """An industry with no coarse-group mapping yields None (no index call)."""
    client = _FakeIndexClient()

    async def fake_get_mcp_client(config):
        return client

    import tradingagents.core.mcp_client as mcp_client

    monkeypatch.setattr(mcp_client, "get_mcp_client", fake_get_mcp_client)
    assert asyncio.run(engine.fetch_industry_return("零号行业", "2026-07-03", 1)) is None
    assert client.calls == []


@pytest.mark.unit
def test_fetch_industry_return_config_override(engine, monkeypatch):
    """reflection_industry_index_map overrides/extends the default map."""
    engine.config = {"reflection_industry_index_map": {"地产": "999999.SI"}}
    client = _FakeIndexClient()

    async def fake_get_mcp_client(config):
        return client

    import tradingagents.core.mcp_client as mcp_client

    monkeypatch.setattr(mcp_client, "get_mcp_client", fake_get_mcp_client)
    result = asyncio.run(engine.fetch_industry_return("地产", "2026-07-03", 1))
    assert result is not None
    assert result["industry_index_symbol"] == "999999.SI"
    assert client.calls == ["999999.SI"]
