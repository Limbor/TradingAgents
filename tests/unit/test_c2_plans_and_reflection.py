"""C-2 + D tests: plans table, plan evaluator, plan monitor, reflection due_date.

Covers the plan lifecycle (plans table + evaluator + close-of-day monitoring)
and the reflection-queue visibility (due_date via advance_trading_days).
"""

import asyncio
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.core.persistence import Database
from tradingagents.core.plan_evaluator import evaluate_plan
from tradingagents.core.plan_monitor import evaluate_active_plans
from tradingagents.core.trading_time import advance_trading_days
from tradingagents.skills.stock_analysis.skill import StockAnalysisSkill


class FakeProvider:
    def __init__(self, price, cross=None, cash=False):
        self._price = price
        self._cross = cross
        self._cash = cash

    async def latest_close(self, symbol):
        return {"close": self._price, "trade_date": "2026-07-07"} if self._price is not None else None

    async def ma_cross(self, symbol):
        return self._cross

    async def cashflow_positive_recent(self, symbol):
        return self._cash


def _db():
    return Database(Path(tempfile.mktemp(suffix=".db")))


def test_plans_db_roundtrip():
    db = _db()
    db.save_plan(
        "plan-1", symbol="600519.SH", entry_zone=[1480.0, 1500.0], stop_loss=1400.0,
        targets=[1650.0], position_pct=5.0, conditions=[{"kind": "stop", "description": "死叉"}],
        rating="Buy", status="active", source="analysis", artifact_id="a1",
    )
    p = db.get_plan("plan-1")
    assert p["entry_zone"] == [1480.0, 1500.0] and p["conditions"][0]["kind"] == "stop"
    db.update_plan("plan-1", status="triggered", trigger_reason="stop_loss hit")
    assert db.get_plan("plan-1")["status"] == "triggered"
    assert len(db.list_plans(status="active")) == 0
    assert len(db.list_plans(status="triggered")) == 1
    assert db.delete_plan("plan-1") == 1


def test_plan_evaluator_price_triggers():
    # stop hit
    r = asyncio.run(evaluate_plan(
        {"symbol": "X", "stop_loss": 1400.0, "targets": [1650.0], "entry_zone": [1480, 1500], "conditions": []},
        data=FakeProvider(price=1390.0),
    ))
    assert r["triggered"] and "stop_loss" in r["reason"]
    # take profit hit
    r = asyncio.run(evaluate_plan(
        {"symbol": "X", "stop_loss": 1400.0, "targets": [1650.0], "conditions": []},
        data=FakeProvider(price=1660.0),
    ))
    assert r["triggered"] and "take_profit" in r["reason"]
    # entry touched, not triggered
    r = asyncio.run(evaluate_plan(
        {"symbol": "X", "stop_loss": 1400.0, "targets": [1650.0], "entry_zone": [1480, 1500], "conditions": []},
        data=FakeProvider(price=1490.0),
    ))
    assert not r["triggered"] and any("entry_zone" in d for d in r["details"])
    # no price
    r = asyncio.run(evaluate_plan(
        {"symbol": "X", "stop_loss": 1400.0, "conditions": []}, data=FakeProvider(price=None),
    ))
    assert not r["triggered"] and r["reason"] == "no latest close"


def test_plan_evaluator_indicator_conditions():
    # golden cross full condition — satisfied but not a trigger (full != stop/take_profit)
    r = asyncio.run(evaluate_plan(
        {"symbol": "X", "conditions": [{"kind": "full", "description": "金叉+现金流", "source": "MA"}]},
        data=FakeProvider(price=10.0, cross="golden", cash=True),
    ))
    assert not r["triggered"]
    assert any("satisfied" in d and "golden" in d for d in r["details"])
    # stop condition via death cross → triggered
    r = asyncio.run(evaluate_plan(
        {"symbol": "X", "conditions": [{"kind": "stop", "description": "死叉", "source": "MACD"}]},
        data=FakeProvider(price=10.0, cross="death"),
    ))
    assert r["triggered"] and "stop" in r["reason"]


def test_evaluate_active_plans_updates_and_alerts():
    db = _db()
    db.save_plan("p-stop", symbol="600519.SH", stop_loss=1400.0, targets=[1650.0],
                 status="active", source="analysis", artifact_id="a1")
    db.save_plan("p-safe", symbol="300750.SZ", stop_loss=100.0, targets=[200.0],
                 status="active", source="analysis", artifact_id="a2")
    # p-stop: price 1390 hits stop 1400; p-safe: price 150, no trigger
    provider = _MultiProvider({"600519.SH": 1390.0, "300750.SZ": 150.0})
    alerts = asyncio.run(evaluate_active_plans(db, {}, data=provider))
    assert len(alerts) == 1
    assert alerts[0]["symbol"] == "600519.SH"
    # Both plans record a check stamp (triggered or not) so the user can tell
    # the daily monitor actually ran, and which trade date it checked up to.
    stop = db.get_plan("p-stop")
    safe = db.get_plan("p-safe")
    assert stop["status"] == "triggered"
    assert stop["last_checked_trade_date"] == "2026-07-07"
    assert stop["last_checked_at"] is not None
    assert safe["status"] == "active"
    assert safe["last_checked_trade_date"] == "2026-07-07"
    assert safe["last_checked_at"] is not None


class _MultiProvider:
    def __init__(self, prices):
        self._prices = prices

    async def latest_close(self, symbol):
        p = self._prices.get(symbol)
        return {"close": p, "trade_date": "2026-07-07"} if p is not None else None

    async def ma_cross(self, symbol):
        return None

    async def cashflow_positive_recent(self, symbol):
        return False


def test_evaluate_active_plans_stamps_last_checked_without_quote():
    """No-quote and erroring plans still record that the monitor ran today."""
    db = _db()
    db.save_plan("p-noquote", symbol="000001.SZ", stop_loss=10.0, targets=[20.0],
                 status="active", source="analysis", artifact_id="a3")
    db.save_plan("p-err", symbol="000002.SZ", stop_loss=10.0, targets=[20.0],
                 status="active", source="analysis", artifact_id="a4")

    class _NoQuoteProvider:
        async def latest_close(self, symbol):
            # p-err raises (exception path); p-noquote returns no quote.
            if symbol == "000002.SZ":
                raise RuntimeError("boom")
            return None

        async def ma_cross(self, symbol):
            return None

        async def cashflow_positive_recent(self, symbol):
            return False

    alerts = asyncio.run(evaluate_active_plans(db, {}, data=_NoQuoteProvider()))
    assert alerts == []
    noquote = db.get_plan("p-noquote")
    err = db.get_plan("p-err")
    # Both record that the monitor attempted them today ...
    assert noquote["last_checked_at"] is not None
    assert err["last_checked_at"] is not None
    # ... but neither fetched a trade date nor triggered.
    assert noquote["last_checked_trade_date"] is None
    assert noquote["status"] == "active"
    assert err["last_checked_trade_date"] is None
    assert err["status"] == "active"


def test_advance_trading_days_returns_iso():
    due = advance_trading_days("2026-07-04", 5)  # 2026-07-04 is Saturday
    assert due is not None
    # should be a valid ISO date >= 5 trading days later
    assert len(due) == 10 and due[4] == "-"
    assert advance_trading_days("2026-07-04", 0) == "2026-07-04"


def test_stock_analysis_enrolls_reflection_case():
    db = _db()
    skill = StockAnalysisSkill()
    skill._save_reflection_case(
        {"db": db, "run_id": "r1"},
        ticker="600519.SH", analysis_date="2026-07-04", rating="Buy",
        structured_conclusion={"plan": {"entry_zone": [10, 11]}, "target_price": 12.0},
        selection_context=None, artifact_id="art-1",
    )
    cases = db.list_reflection_cases(symbol="600519.SH")
    assert len(cases) == 1
    c = cases[0]
    assert c["reflection_scope"] == "decision_grade"
    assert c["eligible_for_strategy_learning"] is True
    assert c["source_type"] == "stock_analysis"
    assert c["source_artifact_id"] == "art-1"
    # Hold rating → candidate_pool, not eligible
    skill._save_reflection_case(
        {"db": db, "run_id": "r1"}, ticker="000001.SZ", analysis_date="2026-07-04",
        rating="Hold", structured_conclusion={}, selection_context=None, artifact_id="art-2",
    )
    hold_case = db.list_reflection_cases(symbol="000001.SZ")[0]
    assert hold_case["reflection_scope"] == "candidate_pool"
    assert hold_case["eligible_for_strategy_learning"] is False


def test_plans_api_and_advance_day_endpoint():
    import os
    db_path = Path(tempfile.mktemp(suffix=".db"))
    os.environ["TRADINGAGENTS_APP_DB"] = str(db_path)
    try:
        app = create_app()
        with TestClient(app) as c:
            # create an active plan
            r = c.post("/api/v1/plans", json={
                "symbol": "600519.SH", "stop_loss": 1400.0, "targets": [1650.0],
                "conditions": [{"kind": "stop", "description": "死叉"}],
                "status": "active", "source": "analysis",
            })
            assert r.status_code == 200
            pid = r.json()["id"]
            # list active
            assert len(c.get("/api/v1/plans?status=active").json()) == 1
            # advance trading day (no holdings → empty refresh; no MCP → no evaluation crash)
            r2 = c.post("/api/v1/portfolio/advance-trading-day")
            assert r2.status_code == 200, r2.text
            body = r2.json()
            assert "refreshed_prices" in body and "plan_alerts" in body and "temporal_context" in body
            assert body["temporal_context"]["market_asof_date"]
            # reflection-cases returns due_date
            c.post("/api/v1/candidate-actions", json={
                "action": "private", "symbol": "600519.SH", "trade_date": "2026-07-04",
                "payload": {"rating": "Buy"},
            })
            cases = c.get("/api/v1/reflection-cases?status=pending").json()
            assert len(cases) >= 1
            assert cases[0]["due_date"] is not None
            # cleanup
            assert c.delete(f"/api/v1/plans/{pid}").json()["deleted"] == 1
    finally:
        os.environ.pop("TRADINGAGENTS_APP_DB", None)
