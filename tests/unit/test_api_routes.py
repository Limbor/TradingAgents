"""Integration tests for API routes using FastAPI TestClient."""

import pytest
from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.core.persistence import Database
from tradingagents.core.run_manager import RunManager, RunStatus
from tradingagents.default_config import DEFAULT_CONFIG


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "api-routes.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "ticker_name_backfill_enabled", False)
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "tradingagents-api"
    assert payload["stockmanager_mcp"]["enabled"] is False
    assert payload["stockmanager_mcp"]["connected"] is False


def test_list_skills(client):
    res = client.get("/api/v1/skills")
    assert res.status_code == 200
    skills = res.json()
    assert len(skills) >= 1
    assert "stock_analysis" in {skill["id"] for skill in skills}


def test_get_skill_schema(client):
    res = client.get("/api/v1/skills/stock_analysis/schema")
    assert res.status_code == 200
    schema = res.json()
    assert "properties" in schema
    assert "ticker" in schema["properties"]


def test_get_skill_schema_not_found(client):
    res = client.get("/api/v1/skills/nonexistent/schema")
    assert res.status_code == 404


def test_list_runs(client):
    res = client.get("/api/v1/runs")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_get_config(client):
    res = client.get("/api/v1/config")
    assert res.status_code == 200
    config = res.json()
    assert "llm_provider" in config
    assert "deep_think_llm" in config
    assert "stockmanager_mcp_url" in config


def test_get_trading_time_context(client):
    res = client.get("/api/v1/trading-time?market=us")
    assert res.status_code == 200
    payload = res.json()
    assert payload["market"] == "us"
    assert payload["market_asof_date"]
    assert payload["decision_target_date"]
    assert payload["info_cutoff"]
    assert payload["data_policy"]["price"] == "asof_market_close"


def test_update_config(client):
    res = client.put(
        "/api/v1/config",
        json={"max_debate_rounds": 3},
    )
    assert res.status_code == 200
    assert res.json()["max_debate_rounds"] == 3


def test_update_config_allows_backend_url_reset(client):
    res = client.put(
        "/api/v1/config",
        json={"backend_url": "https://example.invalid/v1"},
    )
    assert res.status_code == 200
    assert res.json()["backend_url"] == "https://example.invalid/v1"

    res = client.put(
        "/api/v1/config",
        json={"backend_url": None},
    )
    assert res.status_code == 200
    assert res.json()["backend_url"] is None


def test_update_config_persists_across_app_restart(tmp_path, monkeypatch):
    db_path = tmp_path / "persisted-config.db"
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(db_path))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "ticker_name_backfill_enabled", False)

    app = create_app()
    with TestClient(app) as c:
        res = c.put(
            "/api/v1/config",
            json={
                "llm_provider": "deepseek",
                "quick_think_llm": "deepseek-v4-flash",
                "deep_think_llm": "deepseek-v4-pro",
                "backend_url": None,
            },
        )
        assert res.status_code == 200
        assert res.json()["llm_provider"] == "deepseek"

    restarted = create_app()
    with TestClient(restarted) as c:
        res = c.get("/api/v1/config")
        assert res.status_code == 200
        payload = res.json()
        assert payload["llm_provider"] == "deepseek"
        assert payload["quick_think_llm"] == "deepseek-v4-flash"
        assert payload["deep_think_llm"] == "deepseek-v4-pro"


def test_run_manager_reads_persisted_runs(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.save_run("run-1", "market_scanner", {"limit": 3}, "pending")
    db.update_run_status(
        "run-1",
        "completed",
        result={"status": "success"},
        started_at="2026-06-30T08:30:00+00:00",
        completed_at="2026-06-30T08:31:00+00:00",
    )

    manager = RunManager(db=db)
    runs = manager.list_runs()
    assert [run.id for run in runs] == ["run-1"]
    assert runs[0].status is RunStatus.COMPLETED
    assert runs[0].params == {"limit": 3}
    assert runs[0].result == {"status": "success"}

    run = manager.get_run("run-1")
    assert run is not None
    assert run.skill_id == "market_scanner"


def test_profile_get_and_update(client):
    res = client.get("/api/v1/profile")
    assert res.status_code == 200
    assert res.json()["investment_style"] == "long_term"

    res = client.put(
        "/api/v1/profile",
        json={
            "investment_style": "short_term",
            "risk_tolerance": "high",
            "sector_prefs": ["新能源"],
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["investment_style"] == "short_term"
    assert payload["risk_tolerance"] == "high"
    assert payload["sector_prefs"] == ["新能源"]


def test_holdings_crud(client):
    res = client.put(
        "/api/v1/holdings/600519.SH",
        json={
            "symbol": "600519.SH",
            "quantity": 10,
            "avg_cost": 1500,
            "current_price": 1600,
            "notes": "core",
        },
    )
    assert res.status_code == 200
    assert res.json()["symbol"] == "600519.SH"

    res = client.get("/api/v1/holdings")
    assert res.status_code == 200
    assert len(res.json()) == 1

    res = client.delete("/api/v1/holdings/600519.SH")
    assert res.status_code == 200

    res = client.get("/api/v1/holdings")
    assert res.status_code == 200
    assert res.json() == []


def test_holdings_adjust_add_recomputes_avg_cost(client):
    client.put("/api/v1/holdings/600519.SH", json={
        "symbol": "600519.SH", "quantity": 10, "avg_cost": 1500, "current_price": 1600,
    })
    # 加仓 5 @ 1800 -> qty 15, avg = (10*1500 + 5*1800)/15 = 1600, price->1800
    res = client.post("/api/v1/holdings/600519.SH/adjust", json={
        "action": "add", "quantity": 5, "price": 1800,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "add"
    assert body["holding"]["quantity"] == 15
    assert body["holding"]["avg_cost"] == 1600
    assert body["holding"]["current_price"] == 1800
    assert body["realized_pnl"] is None
    assert body["closed"] is False


def test_holdings_adjust_reduce_realizes_pnl(client):
    client.put("/api/v1/holdings/600519.SH", json={
        "symbol": "600519.SH", "quantity": 10, "avg_cost": 1500, "current_price": 1600,
    })
    # 减仓 4 @ 1800 -> qty 6, avg unchanged 1500, realized = 4*(1800-1500)=1200
    res = client.post("/api/v1/holdings/600519.SH/adjust", json={
        "action": "reduce", "quantity": 4, "price": 1800,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["holding"]["quantity"] == 6
    assert body["holding"]["avg_cost"] == 1500
    assert body["realized_pnl"] == 1200
    assert body["closed"] is False


def test_holdings_adjust_reduce_full_close_and_oversell(client):
    client.put("/api/v1/holdings/600519.SH", json={
        "symbol": "600519.SH", "quantity": 10, "avg_cost": 1500, "current_price": 1600,
    })
    # 清仓: sell all 10 @ 1700 -> realized = 10*(1700-1500)=2000, holding deleted
    res = client.post("/api/v1/holdings/600519.SH/adjust", json={
        "action": "reduce", "quantity": 10, "price": 1700,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["holding"] is None
    assert body["closed"] is True
    assert body["realized_pnl"] == 2000
    assert client.get("/api/v1/holdings").json() == []

    # oversell rejected (re-seed first)
    client.put("/api/v1/holdings/600519.SH", json={
        "symbol": "600519.SH", "quantity": 10, "avg_cost": 1500, "current_price": 1600,
    })
    res = client.post("/api/v1/holdings/600519.SH/adjust", json={
        "action": "reduce", "quantity": 11, "price": 1700,
    })
    assert res.status_code == 400


def test_holdings_include_latest_stock_analysis(client):
    res = client.put(
        "/api/v1/holdings/600519.SH",
        json={
            "symbol": "600519.SH",
            "quantity": 10,
            "avg_cost": 1500,
            "current_price": 1600,
            "notes": "core",
        },
    )
    assert res.status_code == 200

    client.app.state.db.save_report(
        report_id="report-holding-1",
        run_id="run-stock-analysis-1",
        ticker="600519.SH",
        ticker_name="贵州茅台",
        rating="BUY",
        content="维持买入。基本面稳定，风险可控。",
        path=None,
    )

    res = client.get("/api/v1/holdings")
    assert res.status_code == 200
    analysis = res.json()[0]["latest_analysis"]
    assert analysis["rating"] == "BUY"
    assert analysis["date"]
    assert analysis["artifact_id"] == "report-holding-1"
    assert analysis["run_id"] == "run-stock-analysis-1"


def test_holdings_accept_name_and_auto_latest_close(client, monkeypatch):
    async def fake_latest_close(symbol, config):
        return {"symbol": symbol, "close": 1688.5, "trade_date": "2026-07-02", "source": "test"}

    monkeypatch.setattr("tradingagents.api.routes.portfolio.latest_close", fake_latest_close)

    res = client.put(
        "/api/v1/holdings/贵州茅台",
        json={
            "symbol": "贵州茅台",
            "quantity": 10,
            "avg_cost": 1500,
            "current_price": None,
            "notes": "core",
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["symbol"] == "600519.SH"
    assert payload["current_price"] == 1688.5


def test_holdings_refresh_prices_updates_current_price(client, monkeypatch):
    async def fake_latest_close(symbol, config):
        return {"symbol": symbol, "close": 20.5, "trade_date": "2026-07-02", "source": "test"}

    monkeypatch.setattr("tradingagents.api.routes.portfolio.latest_close", fake_latest_close)

    res = client.put(
        "/api/v1/holdings/601899",
        json={
            "symbol": "601899",
            "quantity": 100,
            "avg_cost": 18,
            "current_price": 19,
            "notes": None,
        },
    )
    assert res.status_code == 200
    assert res.json()["symbol"] == "601899.SH"

    res = client.post("/api/v1/portfolio/refresh-prices")
    assert res.status_code == 200
    payload = res.json()
    assert payload["updated"] == 1
    assert payload["failed"] == []
    assert payload["holdings"][0]["current_price"] == 20.5


def test_list_reports(client):
    res = client.get("/api/v1/reports")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_get_report_not_found(client):
    res = client.get("/api/v1/reports/nonexistent")
    assert res.status_code == 404


def test_get_run_not_found(client):
    res = client.get("/api/v1/runs/nonexistent")
    assert res.status_code == 404


def test_create_run_invalid_skill(client):
    res = client.post(
        "/api/v1/runs",
        json={"skill_id": "nonexistent", "params": {}},
    )
    assert res.status_code == 404


def test_timeline_endpoint(client):
    """Timeline endpoint returns list and respects since=today."""
    res = client.get("/api/v1/timeline")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    res = client.get("/api/v1/timeline?since=today&limit=5")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
