from __future__ import annotations

import asyncio
import importlib

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tradingagents.api.app import create_app
from tradingagents.core.decision_audit import DecisionAuditEngine, audit_summary
from tradingagents.core.persistence import Database
from tradingagents.core.reflection import ReflectionEngine
from tradingagents.core.strategy_backtest import (
    audit_backtest_result,
    summarize_walk_forward,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.skills.decision_audit.skill import DecisionAuditInput, DecisionAuditSkill
from tradingagents.skills.strategy_backtest.skill import (
    StrategyBacktestInput,
    StrategyBacktestSkill,
)


def seed_decision(db: Database, *, day: str = "2026-01-05") -> dict:
    return db.upsert_decision_record(
        decision_id="decision:test", source_type="daily_pipeline",
        source_run_id="run-1", symbol="600519.SH", name="贵州茅台",
        decision_date=day, decision="BUY", horizon_days=5,
        reference_price=100, payload={"final_score": 0.82},
    )


def test_audit_ledger_links_execution_and_outcome(tmp_path):
    db = Database(tmp_path / "audit.db")
    seed_decision(db)
    execution = db.save_trade_execution(
        execution_id="exec-1", decision_id="decision:test", symbol="600519.SH",
        action="add", quantity=100, price=101, executed_at="2026-01-06T02:00:00Z",
    )
    outcome = db.save_decision_outcome(
        outcome_id="out-1", decision_id="decision:test", horizon_days=5,
        as_of_date="2026-01-12", actual_return=0.08, close_at_signal=100,
        close_at_horizon=108, benchmark_return=0.02, source="test",
    )

    assert execution["decision_id"] == "decision:test"
    assert outcome["excess_return"] == 0.06
    summary = audit_summary(db, min_samples=2)
    assert summary["decision_count"] == 1
    assert summary["execution_link_rate"] == 1.0
    assert summary["realized_count"] == 1
    assert summary["execution_validation"]["sample_count"] == 1
    assert summary["execution_validation"]["win_rate"] == 1.0
    assert summary["validation"]["strategy_claims_allowed"] is False


def test_existing_signals_and_reports_are_backfilled_idempotently(tmp_path):
    path = tmp_path / "migration.db"
    db = Database(path)
    db.save_signal(
        signal_id="legacy-signal", run_id="run-s", trade_date="2025-01-02",
        symbol="600519.SH", name="贵州茅台", signal="BUY", final_score=0.8,
        quant_score=0.8, llm_confidence=None, fusion_mode="quant", payload={},
    )
    db.save_report("legacy-report", "run-r", "000858.SZ", "Sell", "report", None, "五粮液")
    # Simulate reopening an upgraded historical database; the migration is safe
    # to execute repeatedly and does not duplicate rows.
    Database(path)
    reopened = Database(path)
    records = reopened.list_decision_records(limit=20)
    ids = {row["id"] for row in records}
    assert "signal:legacy-signal" in ids
    assert "report:legacy-report" in ids
    assert len([row for row in records if row["id"] == "report:legacy-report"]) == 1


def test_signal_audit_uses_executable_target_date_not_prior_close(tmp_path):
    db = Database(tmp_path / "target-date.db")
    db.save_signal(
        signal_id="target-signal", run_id="run", trade_date="2026-07-10",
        symbol="600519.SH", name="贵州茅台", signal="BUY", final_score=0.8,
        quant_score=0.8, llm_confidence=None, fusion_mode="quant",
        payload={"market_asof_date": "2026-07-10", "decision_target_date": "2026-07-13"},
    )
    decision = db.get_decision_record("signal:target-signal")
    assert decision["decision_date"] == "2026-07-13"
    assert decision["payload"]["market_asof_date"] == "2026-07-10"


def test_cn_outcome_fallback_uses_qfq_series(monkeypatch):
    frame = pd.DataFrame({
        "Date": pd.date_range("2026-01-05", periods=7, freq="B"),
        "Close": [100, 101, 102, 103, 104, 105, 106],
    })
    monkeypatch.setattr("tradingagents.dataflows.akshare_stock.load_ohlcv_cn", lambda *_: frame)
    outcome = asyncio.run(ReflectionEngine(None, {})._fetch_outcome_cn(
        "600519.SH", "2026-01-05", 5
    ))
    assert outcome["actual_return"] == 0.05
    assert outcome["source"] == "akshare_qfq"


def test_due_outcome_creates_reflection_link(tmp_path, monkeypatch):
    db = Database(tmp_path / "due.db")
    seed_decision(db, day="2025-01-02")
    engine = DecisionAuditEngine(db, {})

    async def outcome(*_args, **_kwargs):
        return {"actual_return": 0.05, "close_at_signal": 100,
                "close_at_horizon": 105, "source": "fixture"}

    async def benchmark(*_args, **_kwargs):
        return {"actual_return": 0.02, "source": "fixture-index"}

    monkeypatch.setattr(engine.reflection, "fetch_outcome", outcome)
    monkeypatch.setattr(engine, "_fetch_benchmark", benchmark)
    result = asyncio.run(engine.evaluate_due(as_of_date="2025-03-01", horizons=(5,)))

    assert result["evaluated_outcomes"] == 4
    decision = db.get_decision_record("decision:test")
    assert decision["status"] == "realized"
    case = db.get_reflection_case(decision["reflection_case_id"])
    assert case["status"] == "outcome_ready"
    assert case["outcome_payload"]["actual_return"] == 0.05
    stored = next(row for row in db.list_decision_outcomes(decision_id="decision:test") if row["horizon_days"] == 5)
    assert stored["excess_return"] == pytest.approx(0.03)


def test_primary_outcome_reflects_while_long_horizons_keep_tracking(tmp_path, monkeypatch):
    db = Database(tmp_path / "tracking.db")
    seed_decision(db, day="2025-01-02")
    engine = DecisionAuditEngine(db, {})

    async def outcome(*_args, **_kwargs):
        return {"actual_return": 0.01, "close_at_signal": 100,
                "close_at_horizon": 101, "source": "fixture"}

    async def benchmark(*_args, **_kwargs):
        return None

    monkeypatch.setattr(engine.reflection, "fetch_outcome", outcome)
    monkeypatch.setattr(engine, "_fetch_benchmark", benchmark)
    asyncio.run(engine.evaluate_due(as_of_date="2025-01-10"))

    decision = db.get_decision_record("decision:test")
    assert decision["status"] == "tracking"
    assert db.get_reflection_case(decision["reflection_case_id"])["status"] == "outcome_ready"
    assert {row["horizon_days"] for row in db.list_decision_outcomes(decision_id=decision["id"])} == {1, 5}


def test_decision_audit_api_and_confirmed_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "api.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)
    with TestClient(create_app()) as client:
        db = client.app.state.db
        seed_decision(db)
        db.upsert_holding("600519.SH", 200, 100, 110, None)
        response = client.post("/api/v1/holdings/600519.SH/adjust", json={
            "action": "reduce", "quantity": 100, "price": 110,
            "decision_id": "decision:test",
        })
        assert response.status_code == 200
        assert response.json()["execution"]["decision_id"] == "decision:test"
        assert db.get_decision_record("decision:test")["status"] == "partially_realized"

        summary = client.get("/api/v1/decision-audit/summary")
        assert summary.status_code == 200
        assert summary.json()["linked_execution_count"] == 1

        external = client.post("/api/v1/decision-audit/executions", json={
            "decision_id": "decision:test", "symbol": "600519.SH",
            "action": "sell", "quantity": 50, "price": 112,
        })
        assert external.status_code == 200
        assert external.json()["source"] == "external_confirmed"
        mismatch = client.post("/api/v1/decision-audit/executions", json={
            "decision_id": "decision:test", "symbol": "000001.SZ",
            "action": "buy", "quantity": 50, "price": 10,
        })
        assert mismatch.status_code == 400


def test_backtest_job_submission_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_APP_DB", str(tmp_path / "backtest.db"))
    monkeypatch.setitem(DEFAULT_CONFIG, "stockmanager_mcp_enabled", False)
    monkeypatch.setitem(DEFAULT_CONFIG, "scheduler_enabled", False)

    class MCP:
        async def list_strategies_and_configs(self):
            return {"strategies": [{"name": "ff_residual_csi800_main", "sha1": "s1"}],
                    "configs": [{"name": "prod_ff_residual_csi800_tv15", "sha1": "c1"}]}

        async def run_backtest(self, **kwargs):
            assert kwargs["strategy"] == "ff_residual_csi800_main"
            assert kwargs["config"] == "prod_ff_residual_csi800_tv15"
            return {"job_id": "job-1", "status": "queued"}

        async def get_job_status(self, job_id):
            assert job_id == "job-1"
            return {"status": "completed"}

        async def get_job_result(self, job_id):
            return {"total_return": 0.12, "max_drawdown": -0.08, "sharpe": 1.1,
                    "win_rate": 0.55, "turnover": 1.2, "source": "stockmanager",
                    "data_version": "fixture-v1", "lookahead_bias_check_passed": True,
                    "survivorship_bias_check_passed": True,
                    "transaction_cost_bps": 10, "slippage_bps": 5,
                    "equity_curve": [{"date": "2024-01-01", "value": 1.0}]}

        async def compute_purged_cv_sharpe(self, equity_curve, n_splits, purge_days):
            return {"mean_sharpe": 0.9, "splits": n_splits, "purge_days": purge_days}

    async def get_client(_config):
        return MCP()

    monkeypatch.setattr("tradingagents.api.routes.decision_audit.get_mcp_client", get_client)
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/backtests", json={
            "start_date": "2024-01-01", "end_date": "2025-01-01",
            "transaction_cost_bps": 12, "slippage_bps": 6,
        })
        assert response.status_code == 200
        item = response.json()
        assert item["status"] == "submitted"
        assert item["config"]["strategy_sha1"] == "s1"
        assert item["config"]["config_sha1"] == "c1"
        duplicate = client.post("/api/v1/backtests", json={
            "start_date": "2024-01-01", "end_date": "2025-01-01",
            "transaction_cost_bps": 12, "slippage_bps": 6,
        })
        assert duplicate.json()["id"] == item["id"]
        recovered = client.get(f"/api/v1/backtests/{item['id']}")
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "completed"
        assert recovered.json()["result"]["max_drawdown"] == -0.08
        assert recovered.json()["result"]["validation"]["production_gate_passed"] is True
        artifacts = client.app.state.db.list_artifacts(artifact_type="backtest_report")
        assert artifacts[0]["payload"]["id"] == item["id"]


def test_strategy_backtest_skill_persists_audited_sync_result(tmp_path, monkeypatch):
    db = Database(tmp_path / "skill-backtest.db")

    class MCP:
        async def list_strategies_and_configs(self):
            return {"strategies": [{"name": "ff_residual_csi800_main", "sha1": "s1"}],
                    "configs": [{"name": "prod_ff_residual_csi800_tv15", "sha1": "c1"}]}

        async def run_backtest(self, **_kwargs):
            return {"total_return": 0.1, "max_drawdown": -0.06, "sharpe": 1.0,
                    "win_rate": 0.52, "turnover": 1.1, "source": "stockmanager",
                    "data_version": "fixture-v1", "lookahead_bias_check_passed": True,
                    "survivorship_bias_check_passed": True,
                    "transaction_cost_bps": 10, "slippage_bps": 5,
                    "equity_curve": [{"date": "2024-01-01", "value": 1.0}]}

        async def compute_purged_cv_sharpe(self, *_args, **_kwargs):
            return {"mean_sharpe": 0.8}

    async def get_client(_config):
        return MCP()

    module = importlib.import_module("tradingagents.skills.strategy_backtest.skill")
    monkeypatch.setattr(module, "get_mcp_client", get_client)

    async def run():
        events = []
        params = StrategyBacktestInput(start_date="2024-01-01", end_date="2025-01-01")
        async for event in StrategyBacktestSkill().execute(params, {"db": db}):
            events.append(event)
        return events

    events = asyncio.run(run())
    assert events[-1].data["status"] == "success"
    item = db.list_backtest_runs()[0]
    assert item["status"] == "completed"
    assert item["result"]["validation"]["production_gate_passed"] is True
    assert db.list_artifacts(artifact_type="backtest_report")


def test_backtest_gate_rejects_missing_provenance_and_lookahead_evidence():
    class MCP:
        async def compute_purged_cv_sharpe(self, *_args, **_kwargs):
            return {"mean_sharpe": 1.0}

    result = asyncio.run(audit_backtest_result(
        MCP(),
        {"total_return": 0.1, "max_drawdown": -0.1, "sharpe": 1.0,
         "win_rate": 0.5, "turnover": 1.0,
         "equity_curve": [{"date": "2024-01-01", "value": 1.0}]},
        {"holding_days": 5, "transaction_cost_bps": 10, "slippage_bps": 5},
    ))
    assert result["validation"]["production_gate_passed"] is False
    assert result["validation"]["missing_provenance"] == ["source", "data_version"]
    assert result["validation"]["lookahead_bias_check_passed"] is False


def test_summarize_walk_forward_from_fold_sharpes():
    """Per-fold sharpes drive consistency + weakest-fold + OOS-decay summary."""
    summary = summarize_walk_forward(
        {
            "mean_sharpe": 0.9,
            "fold_sharpes": [1.2, 0.8, 0.4, 1.0, 0.6],
            "is_sharpe": 1.3,
            "oos_sharpe": 0.9,
        }
    )
    assert summary["available"] is True
    assert summary["n_folds"] == 5
    assert summary["positive_fold_ratio"] == 1.0
    assert summary["min_fold_sharpe"] == 0.4
    assert summary["consistent"] is True
    assert summary["oos_decay"] == 0.4


def test_summarize_walk_forward_flags_inconsistent_folds():
    """A negative fold makes the walk-forward inconsistent (informational only)."""
    summary = summarize_walk_forward(
        {"splits": [{"oos_sharpe": 1.1}, {"sharpe": -0.3}, {"oos_sharpe": 0.5}]}
    )
    assert summary["n_folds"] == 3
    assert summary["positive_fold_ratio"] == round(2 / 3, 4)
    assert summary["consistent"] is False


def test_summarize_walk_forward_graceful_without_folds():
    """No per-fold detail -> available False so the UI can degrade."""
    summary = summarize_walk_forward({"mean_sharpe": 0.9})
    assert summary["available"] is False
    assert summary["n_folds"] == 0


def test_audit_attaches_walk_forward_slippage_and_ablation():
    """When the result carries trades and the config declares ablations, the
    audit surfaces execution slippage + ablation contribution alongside the
    walk-forward summary — without gating production readiness on them."""
    calls: dict[str, object] = {}

    class MCP:
        async def compute_purged_cv_sharpe(self, equity_curve, n_splits, purge_days):
            return {"mean_sharpe": 0.9, "fold_sharpes": [1.0, 0.8, 0.6]}

        async def analyze_execution_slippage(self, trades, participation_rates=None):
            calls["slippage_trades"] = len(trades)
            calls["participation"] = participation_rates
            return {"avg_slippage_bps": 7.5, "total_cost": 1234.0}

        async def run_ablation_study(self, base_experiment, ablations):
            calls["ablations"] = list(ablations)
            return {"base": {"sharpe": 1.0}, "variants": [{"name": "no_momentum", "sharpe": 0.4}]}

    result = asyncio.run(audit_backtest_result(
        MCP(),
        {"total_return": 0.1, "max_drawdown": -0.1, "sharpe": 1.0,
         "win_rate": 0.5, "turnover": 1.0, "source": "stockmanager",
         "data_version": "v1", "lookahead_bias_check_passed": True,
         "survivorship_bias_check_passed": True,
         "transaction_cost_bps": 10, "slippage_bps": 5,
         "equity_curve": [{"date": "2024-01-01", "value": 1.0}],
         "trades": [{"symbol": "600519.SH", "side": "buy", "qty": 100}]},
        {"transaction_cost_bps": 10, "slippage_bps": 5,
         "participation_rates": [0.1, 0.2],
         "backtest_base_experiment": {"strategy": "ff_residual"},
         "backtest_ablations": [{"disable": "momentum"}]},
    ))
    validation = result["validation"]
    assert validation["production_gate_passed"] is True
    assert validation["walk_forward"]["consistent"] is True
    assert validation["execution_slippage"]["available"] is True
    assert validation["execution_slippage"]["avg_slippage_bps"] == 7.5
    assert validation["execution_slippage"]["trade_count"] == 1
    assert validation["ablation_study"]["available"] is True
    assert validation["ablation_study"]["n_ablations"] == 1
    assert calls["slippage_trades"] == 1
    assert calls["participation"] == [0.1, 0.2]
    assert calls["ablations"] == [{"disable": "momentum"}]


def test_audit_skips_addons_without_inputs_or_support():
    """No trades / no declared ablations -> add-ons report unavailable and the
    core gate is unaffected (backward compatible with plain MCP mocks)."""
    class MCP:
        async def compute_purged_cv_sharpe(self, *_args, **_kwargs):
            return {"mean_sharpe": 1.0}

    result = asyncio.run(audit_backtest_result(
        MCP(),
        {"total_return": 0.1, "max_drawdown": -0.1, "sharpe": 1.0,
         "win_rate": 0.5, "turnover": 1.0, "source": "stockmanager",
         "data_version": "v1", "lookahead_bias_check_passed": True,
         "survivorship_bias_check_passed": True,
         "transaction_cost_bps": 10, "slippage_bps": 5,
         "equity_curve": [{"date": "2024-01-01", "value": 1.0}]},
        {"transaction_cost_bps": 10, "slippage_bps": 5},
    ))
    validation = result["validation"]
    assert validation["production_gate_passed"] is True
    assert validation["execution_slippage"]["available"] is False
    assert validation["execution_slippage"]["reason"] == "no_trades"
    assert validation["ablation_study"]["available"] is False
    assert validation["ablation_study"]["reason"] == "no_base_experiment"


def test_decision_audit_skill_writes_report_artifact(tmp_path):
    db = Database(tmp_path / "audit-skill.db")
    seed_decision(db, day="2025-01-02")
    for horizon in (1, 5, 10, 20):
        db.save_decision_outcome(
            outcome_id=f"ready:{horizon}", decision_id="decision:test", horizon_days=horizon,
            as_of_date="2025-02-01", actual_return=0.03,
            close_at_signal=100, close_at_horizon=103, source="fixture",
        )

    async def run():
        events = []
        async for event in DecisionAuditSkill().execute(
            DecisionAuditInput(as_of_date="2025-02-01", horizons=[5]),
            {"db": db, "run_id": "audit-run"},
        ):
            events.append(event)
        return events

    events = asyncio.run(run())
    assert events[-1].data["status"] == "success"
    artifact = db.list_artifacts(artifact_type="decision_audit_report")[0]
    assert artifact["run_id"] == "audit-run"
    assert "有效性声明门禁" in artifact["content_markdown"]
