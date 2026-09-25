"""Tests for the /api/v1/market/overview endpoint."""

from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.default_config import DEFAULT_CONFIG


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "market-api.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "ticker_name_backfill_enabled", False)
    app = create_app()
    with TestClient(app) as c:
        yield c


def _save_overview_artifact(client, asof_date: str) -> None:
    client.app.state.db.save_artifact(
        artifact_id=f"market-overview-cn_a-{asof_date}",
        run_id="run-test",
        skill_id="market_overview",
        artifact_type="market_overview",
        title="市场全景",
        subject_type="market",
        subject_id="cn_a",
        payload={"market_asof_date": asof_date, "regime": None, "degraded": []},
    )


def test_market_overview_empty(client):
    res = client.get("/api/v1/market/overview")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is False
    assert body["artifact"] is None
    assert body["is_stale"] is False
    assert body["current_asof_date"]


def test_market_overview_fresh_artifact(client):
    # Use the API's own notion of "current" so the test is calendar-agnostic.
    current = client.get("/api/v1/market/overview").json()["current_asof_date"]
    _save_overview_artifact(client, current)

    body = client.get("/api/v1/market/overview").json()
    assert body["available"] is True
    assert body["is_stale"] is False
    assert body["artifact"]["id"] == f"market-overview-cn_a-{current}"
    assert body["artifact"]["payload"]["market_asof_date"] == current


def test_market_overview_stale_artifact(client):
    _save_overview_artifact(client, "2000-01-04")

    body = client.get("/api/v1/market/overview").json()
    assert body["available"] is True
    assert body["is_stale"] is True
    assert body["artifact"]["payload"]["market_asof_date"] == "2000-01-04"


def test_market_overview_backfills_and_filters_old_artifact_taxonomy(client):
    """Old snapshots become investable immediately without a full refresh."""
    client.app.state.db.save_artifact(
        artifact_id="market-overview-old-taxonomy",
        run_id="run-test",
        skill_id="market_overview",
        artifact_type="market_overview",
        title="市场全景",
        subject_type="market",
        subject_id="cn_a",
        payload={
            "market_asof_date": "2026-08-20",
            "industry_stances": [
                {"industry": "CXO", "board_type": "concept"},
                {"industry": "跨行业主题", "board_type": "concept"},
                {"industry": "鸿蒙", "board_type": "concept"},
                {"industry": "华为鸿蒙", "board_type": "concept"},
                {"industry": "华为", "board_type": "concept"},
            ],
        },
    )

    stances = client.get("/api/v1/market/overview").json()["artifact"]["payload"][
        "industry_stances"
    ]
    assert stances[0]["selection_industries"] == ["医药生物"]
    assert stances[0]["selection_concept"] == "CXO"
    assert stances[0]["selection_mode"] == "proxy"
    assert len(stances) == 2
    harmony = stances[1]
    assert harmony["industry"] == "鸿蒙生态"
    assert harmony["source_names"] == ["鸿蒙", "华为鸿蒙"]
    assert harmony["selection_industries"] == ["计算机"]


def test_market_overview_skill_registered(client):
    res = client.get("/api/v1/skills")
    assert res.status_code == 200
    assert "market_overview" in {skill["id"] for skill in res.json()}


def test_trade_review_uses_mcp_authoritative_gate(client, monkeypatch):
    class FakeMcp:
        status = SimpleNamespace(tools=["get_trade_review_snapshot"])

        async def get_trade_review_snapshot(self, *args, **kwargs):
            return {
                "status": "success",
                "candles": [{"trade_date": "2026-07-31", "close": 10}],
                "pretrade_gate": {"status": "actionable", "reliability_score": 82},
            }

    async def fake_client(_config):
        return FakeMcp()

    monkeypatch.setattr("tradingagents.api.routes.market.get_mcp_client", fake_client)
    body = client.post(
        "/api/v1/market/trade-review",
        json={"symbol": "600519.SH", "trade_date": "2026-07-31", "plan": {"action_zone": [9, 11]}},
    ).json()
    assert body["pretrade_gate"]["status"] == "actionable"
    assert body["gate_authority"] == "stockmanager_mcp"
    assert body["degraded"] is False
    assert body["recommendation_reliability"]["score"] > 0


def test_trade_review_attaches_optional_mcp_chip_profile_without_overriding_gate(client, monkeypatch):
    class FakeMcp:
        status = SimpleNamespace(
            tools=["get_trade_review_snapshot", "get_chip_distribution_snapshot"]
        )

        async def get_trade_review_snapshot(self, *args, **kwargs):
            return {
                "status": "success",
                "candles": [{"trade_date": "2026-07-31", "close": 10}],
                "warnings": [],
                "plan": {},
                "pretrade_gate": {
                    "status": "wait", "reliability_score": 82,
                    "reasons": ["等待价格触发"], "checks": [],
                },
            }

        async def get_chip_distribution_snapshot(self, *args, **kwargs):
            return {
                "status": "available",
                "method": "turnover_decay_triangular_v1",
                "advisory_only": True,
                "current": {"avg_cost": 9.8},
                "trend": {"state": "bullish_confirmed", "confirmation_score": 75},
                "series": [],
            }

    async def fake_client(_config):
        return FakeMcp()

    monkeypatch.setattr("tradingagents.api.routes.market.get_mcp_client", fake_client)
    body = client.post(
        "/api/v1/market/trade-review",
        json={"symbol": "600519.SH", "trade_date": "2026-07-31"},
    ).json()

    assert body["chip_profile"]["status"] == "available"
    assert body["chip_profile"]["advisory_only"] is True
    assert body["pretrade_gate"]["status"] == "wait"


def test_trade_review_retries_transient_mcp_failure_and_caps_monitor(client, monkeypatch):
    class FlakyMcp:
        status = SimpleNamespace(tools=["get_trade_review_snapshot"])
        calls = 0

        async def get_trade_review_snapshot(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return None
            return {
                "status": "success",
                "candles": [{"trade_date": "2026-07-31", "close": 10}],
                "effective_trade_date": "2026-07-31",
                "pretrade_gate": {
                    "status": "actionable", "reliability_score": 90,
                    "reasons": [], "checks": [],
                },
            }

    mcp = FlakyMcp()

    async def fake_client(_config):
        return mcp

    monkeypatch.setattr("tradingagents.api.routes.market.get_mcp_client", fake_client)
    body = client.post(
        "/api/v1/market/trade-review",
        json={
            "symbol": "603341.SH", "trade_date": "2026-07-31",
            "recommendation_context": {
                "final_decision": "MONITOR", "quant_decision": "WATCHLIST",
            },
        },
    ).json()
    assert mcp.calls == 2
    assert body["pretrade_gate"]["status"] == "wait"
    assert any(
        item["code"] == "recommendation_eligibility"
        for item in body["pretrade_gate"]["checks"]
    )


def test_trade_review_uses_legacy_mcp_daily_for_historical_chart(client, monkeypatch):
    class LegacyMcp:
        status = SimpleNamespace(tools=["get_stock_daily"])

        async def get_stock_daily(self, *args, **kwargs):
            return {"rows": {"600519.SH": [
                {
                    "index": day.strftime("%Y-%m-%d"),
                    "open": 10 + index, "high": 11 + index, "low": 9 + index,
                    "close": 10.5 + index, "volume": 1000,
                }
                for index, day in enumerate(pd.bdate_range("2024-01-02", periods=30))
            ]}}

    async def fake_client(_config):
        return LegacyMcp()

    monkeypatch.setattr("tradingagents.api.routes.market.get_mcp_client", fake_client)
    body = client.post(
        "/api/v1/market/trade-review",
        json={"symbol": "600519.SH", "trade_date": "2024-02-29"},
    ).json()
    assert len(body["candles"]) == 30
    assert body["source"] == "stockmanager_legacy_daily"
    assert body["degraded"] is True
    assert body["pretrade_gate"]["status"] == "wait"


def test_trade_review_fallback_never_marks_actionable(client, monkeypatch):
    async def no_mcp(_config):
        return None

    dates = pd.bdate_range("2026-05-01", periods=70)
    frame = pd.DataFrame({
        "Date": dates,
        "Open": range(70), "High": range(1, 71), "Low": range(70),
        "Close": range(1, 71), "Volume": [1000] * 70,
    })
    monkeypatch.setattr("tradingagents.api.routes.market.get_mcp_client", no_mcp)
    monkeypatch.setattr("tradingagents.dataflows.akshare_stock.load_ohlcv_cn", lambda *_: frame)
    body = client.post(
        "/api/v1/market/trade-review",
        json={"symbol": "600519.SH", "trade_date": "2026-07-31"},
    ).json()
    assert body["pretrade_gate"]["status"] == "wait"
    assert body["gate_authority"] == "none"
    assert body["degraded"] is True
    assert body["recommendation_reliability"]["score"] <= 35
    assert max(item["trade_date"] for item in body["candles"]) <= "2026-07-31"


def test_candidate_plan_requires_authoritative_actionable_review(client):
    blocked = client.post(
        "/api/v1/candidate-actions",
        json={"action": "adopt", "symbol": "600519.SH", "payload": {"final_decision": "BUY"}},
    ).json()
    assert blocked["status"] == "blocked"
    assert blocked["plan_id"] is None

    saved = client.post(
        "/api/v1/candidate-actions",
        json={
            "action": "adopt",
            "symbol": "600519.SH",
            "trade_date": "2026-07-31",
            "payload": {
                "final_decision": "BUY",
                "entry_zone": [1450, 1480],
                "stop_loss": 1400,
                "targets": [1600],
                "trade_review": {
                    "gate_authority": "stockmanager_mcp",
                    "effective_trade_date": "2026-07-31",
                    "pretrade_gate": {"status": "actionable"},
                    "recommendation_reliability": {"score": 82},
                },
            },
        },
    ).json()
    assert saved["status"] == "saved"
    assert saved["plan_id"]
    plan = client.get(f"/api/v1/plans/{saved['plan_id']}").json()
    assert plan["status"] == "active"
    assert plan["action_zone"] == [1450, 1480]
    assert plan["conditions"][0]["source"] == "stockmanager_mcp"


def test_wait_trigger_creates_monitored_but_non_learning_plan(client):
    saved = client.post(
        "/api/v1/candidate-actions",
        json={
            "action": "wait_trigger",
            "symbol": "000001.SZ",
            "trade_date": "2026-07-31",
            "payload": {
                "entry_zone": [10, 10.5], "stop_loss": 9.5, "targets": [12],
                "trade_review": {
                    "gate_authority": "stockmanager_mcp",
                    "pretrade_gate": {"status": "wait"},
                },
            },
        },
    ).json()
    assert saved["plan_id"]
    case = client.get("/api/v1/reflection-cases?symbol=000001.SZ").json()[0]
    assert case["reflection_scope"] == "candidate_pool"
    assert case["eligible_for_strategy_learning"] is False
