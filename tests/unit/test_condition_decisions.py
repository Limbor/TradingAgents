"""Condition suggestions must be traceable without inventing trades or memory effects."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.api.routes.memory_evaluation import replay_samples, router
from tradingagents.core.decision_support import (
    build_decision_brief,
    decision_requested,
    evaluate_decision_path,
    evaluation_horizon,
)
from tradingagents.core.persistence import Database
from tradingagents.core.reflection import (
    ReflectionEngine,
    _build_attribution_prompt,
    _heuristic_attribution,
)
from tradingagents.core.reflection_enroll import enroll_reflection_case
from tradingagents.core.research_metadata import resolve_research_industry
from tradingagents.core.strategy_memory import aggregate_memory_trace
from tradingagents.skills.documents import load_skill_document
from tradingagents.skills.stock_analysis.skill import StockAnalysisSkill


def condition(operator="gte", threshold=10, **changes):
    return {"description": "核对收盘价格", "metric": "close", "source": "本轮未复权日线",
            "operator": operator, "threshold": threshold, "confirmation": "收盘确认", "price_basis": "none", **changes}


def brief(**changes):
    return {"action_state": "wait_trigger", "price_basis": "none", "entry_conditions": [condition()],
            "exit_conditions": [condition("lte", 9)], **changes}


def bars(*closes):
    return [{"trade_date": f"2026-01-{i + 1:02}", "open": 10 + i / 10, "close": close} for i, close in enumerate(closes)]


def test_unknown_entry_and_price_basis_never_assumed_true():
    rows = bars(10, 11, 12, 13)
    for missing in ({"metric": "event"}, {"source": ""}, {"price_basis": "qfq"}, {"price_basis": "unknown"}):
        result = evaluate_decision_path(brief(entry_conditions=[condition(**missing)]), rows)
        assert result["status"] == "unverifiable" and result["was_correct"] is None
        assert "net_return" not in result
    # AND semantics: a known failed prerequisite is enough to reject entry.
    assert evaluate_decision_path(brief(entry_conditions=[condition(threshold=99), condition(metric="event")]), rows)["status"] == "not_triggered"


def test_close_trigger_uses_next_open_and_t_plus_one_exit():
    rows = bars(10, 8, 12, 14)
    result = evaluate_decision_path(brief(), rows)
    assert result["status"] == "triggered"
    assert result["signal_date"] == rows[0]["trade_date"]
    assert result["entry_date"] == rows[1]["trade_date"]
    assert result["exit_date"] == rows[2]["trade_date"]
    assert result["entry_price"] == rows[1]["open"]
    assert result["exit_price"] == rows[2]["open"]
    assert result["net_return"] < result["gross_return"]
    assert result["max_adverse_close_return"] < 0
    assert result["was_correct"] is None


def test_late_trigger_no_round_trip_or_hindsight_failure():
    assert evaluate_decision_path(brief(), bars(7, 8, 9, 10))["status"] == "awaiting_next_session"
    assert evaluate_decision_path(brief(), bars(7, 8, 10, 11))["status"] == "awaiting_exit_session"
    no_entry = evaluate_decision_path(brief(entry_conditions=[condition(threshold=50)]), bars(10, 11, 12))
    assert no_entry["status"] == "not_triggered" and "net_return" not in no_entry
    with pytest.raises(ValueError):
        evaluate_decision_path(brief(), bars(10, 11, 12), fee_bps=float("nan"))
    assert evaluate_decision_path(brief(exit_conditions=[condition(metric="event")]), bars(10, 11, 12))["status"] == "unverifiable"


def test_primary_brief_validation_and_periods():
    args = {"template": "full", "as_of_date": "2026-01-01", "style": "medium_term", "horizon_days": 60}
    invalid = build_decision_brief({}, raw={"action_state": "BUY_NOW"}, **args)
    assert invalid["action_state"] == "insufficient_evidence"
    raw = {**brief(), "summary": "等待业绩确认", "action_state": "consider_entry", "exit_conditions": []}
    result = build_decision_brief({}, raw=raw, **args)
    assert result["action_state"] == "wait_trigger"
    research = build_decision_brief({}, **{**args, "template": "research"})
    assert research["action_state"] == "insufficient_evidence"
    assert [evaluation_horizon(style) for style in ("short_term", "medium_term", "long_term")] == [5, 60, 120]
    assert decision_requested("该股票中线还能投资吗")
    assert decision_requested("600000.SH：把进入和退出条件说具体")
    assert not decision_requested("补充行业营收和现金流")


def enroll(db, run="one", **changes):
    enroll_reflection_case(db, source_type="stock_analysis", symbol="600000.SH", name="浦发银行",
        signal_date="2026-01-01", rating_or_decision="Buy", source_run_id=run, source_artifact_id=run,
        snapshot_payload={"decision_brief": brief(), "info_cutoff": "2026-01-01"}, horizon_days=60,
        immutable=True, **changes)


def test_versioned_snapshot_survives_retry_restart_pruning_and_mutation(tmp_path):
    path = tmp_path / "cases.db"
    db = Database(path)
    enroll(db)
    first = db.list_reflection_cases()[0]
    db.update_reflection_case(first["id"], status="reflected", outcome_payload={"actual_return": .1})
    enroll(db)  # Same report retry must not reset its evaluated result.
    assert db.get_reflection_case(first["id"])["status"] == "reflected"
    enroll(db, "two")
    second = next(row for row in db.list_reflection_cases() if row["id"] != first["id"])
    assert second["snapshot_version"] == 2 and second["supersedes_id"] == first["id"]
    with db._conn() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE reflection_cases SET snapshot_payload='{}' WHERE id=?", (first["id"],))
    with db._conn() as conn:
        conn.execute("UPDATE reflection_cases SET updated_at='2000-01-01' WHERE id=?", (first["id"],))
    assert db.prune_reflection_cases() == 0
    assert len(Database(path).list_reflection_cases()) == 2


def test_legacy_schema_additive_migration(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(path)
    db.save_reflection_case("old", "system_signal", "candidate_pool", False, "600000.SH", "2026-01-01")
    with db._conn() as conn:
        conn.execute("DROP TRIGGER reflection_snapshot_immutable")
        conn.execute("ALTER TABLE reflection_cases DROP COLUMN snapshot_version")
        conn.execute("ALTER TABLE reflection_cases DROP COLUMN supersedes_id")
    migrated = Database(path).get_reflection_case("old")
    assert migrated["snapshot_version"] == 0 and migrated["supersedes_id"] == ""


def test_research_not_enrolled_and_medium_decision_not_five_days(tmp_path):
    db = Database(tmp_path / "analysis.db")
    skill = StockAnalysisSkill()
    kwargs = {"config": {"db": db, "investment_style": "medium_term", "run_id": "run"},
                  "ticker": "600000.SH", "analysis_date": "2026-01-01", "rating": "Buy", "selection_context": None, "artifact_id": "report"}
    skill._save_reflection_case(structured_conclusion={"decision_status": "research_only"}, **kwargs)
    skill._save_reflection_case(structured_conclusion={"decision_brief": {"action_state": "insufficient_evidence"}}, **kwargs)
    assert db.list_reflection_cases() == []
    skill._save_reflection_case(structured_conclusion={"decision_brief": {**brief(), "horizon_days": 60}}, **kwargs)
    assert db.list_reflection_cases()[0]["horizon_days"] == 60


@pytest.mark.asyncio
async def test_metadata_available_and_failure_does_not_discard_research(monkeypatch):
    client = SimpleNamespace(get_industry_map=AsyncMock(return_value={"rows": [{"ts_code": "600000.SH", "industry": "银行"}]}))
    monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client", AsyncMock(return_value=client))
    result = await resolve_research_industry("600000.SS", {})
    assert result["industry"] == "银行" and result["purpose"] == "memory_matching_only"
    client.get_industry_map.side_effect = TimeoutError()
    assert (await resolve_research_industry("600000.SH", {}))["status"] == "unavailable"


@pytest.mark.asyncio
async def test_condition_window_never_uses_short_or_future_sample(monkeypatch):
    rows = [{**row, "trade_date": row["trade_date"].replace("-", "")} for row in bars(10, 11, 12, 13)]
    client = SimpleNamespace(get_stock_daily=AsyncMock(return_value={"rows": rows}))
    monkeypatch.setattr("tradingagents.core.mcp_client.get_mcp_client", AsyncMock(return_value=client))
    engine = ReflectionEngine(None, {})
    assert await engine.fetch_condition_outcome("600000.SH", "2026-01-01", 60, brief()) is None
    outcome = await engine.fetch_condition_outcome("600000.SH", "2026-01-01", 3, brief())
    assert outcome["as_of_date"] == "2026-01-04"
    assert client.get_stock_daily.call_args.kwargs["adj_type"] == "none"
    client.get_stock_daily.return_value = {"rows": rows, "adj_type": "qfq"}
    assert await engine.fetch_condition_outcome("600000.SH", "2026-01-01", 3, brief()) is None


def test_condition_hindsight_never_creates_automatic_lesson():
    case = {"snapshot_payload": {"decision_brief": brief()}, "eligible_for_strategy_learning": True}
    outcome = {"was_correct": None, "actual_return": .8}
    assert _heuristic_attribution(case, outcome, {})["attribution"] == "inconclusive"
    assert "中性决策特判不适用" in _build_attribution_prompt(case, outcome, {})
    assert ReflectionEngine(None, {}).maybe_create_strategy_lesson(case, {"attribution": "missed_upside", "confidence": "high"}) == {}
    assert load_skill_document("reflection").execution == "workflow"


def test_specialist_nested_memory_receipt_and_untrusted_ids():
    trace = {"snapshots": [{"id": "L", "finding": "旧经验"}], "injected_ids": ["L"]}
    record = {"kind": "model", "role": "portfolio_manager", "memory_refs": ["L"],
              "output": {"parsed": {"memory_usage": [{"lesson_id": "L", "status": "referenced", "reason": "核对缺口"},
                                                     {"lesson_id": "fake", "status": "referenced", "reason": "伪造"}]}}}
    merged = aggregate_memory_trace([trace], [record])
    assert merged["injected_ids"] == ["L"]
    assert len(merged["usage"]) == 1 and merged["usage"][0]["role"] == "portfolio_manager"
    assert aggregate_memory_trace([], [{**record, "output": {"memory_usage": "invalid"}}])["usage"] == []


def test_replay_preview_rejects_private_future_import_and_missing_memory(tmp_path):
    db = Database(tmp_path / "replay.db")
    db.save_reflection_case("private", "user_private", "candidate_pool", False, "600000.SH", "2026-01-01", status="reflected",
                           snapshot_payload={"candidate": {"symbol": "600000.SH", "quant_score": 80}}, outcome_payload={"excess_return": .1})
    assert replay_samples(db, "medium_term") == []
    app = FastAPI()
    app.state.db = db
    app.state.config = {}
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/strategy-memory/evaluation-preview").json()["can_run"] is False
    assert client.post("/strategy-memory/evaluations", json={"limit": 20}).status_code == 422
    assert db.list_agent_runtime("anything") == []
    assert db.list_artifacts(artifact_type="memory_evaluation_job") == []


@pytest.mark.asyncio
async def test_explicit_replay_records_progress_real_receipts_and_frozen_inputs(tmp_path, monkeypatch):
    import uuid

    from starlette.requests import Request

    from tradingagents.api.routes import memory_evaluation as api
    from tradingagents.core.agent_runtime import current_context

    db = Database(tmp_path / 'paired.db')
    app = FastAPI()
    app.state.db = db
    app.state.config = {'llm_provider': 'qianwen', 'model_policy': {'default_model': 'qwen3.8-max'}}
    request = Request({'type': 'http', 'app': app})
    samples = [{'trade_date': '2026-01-01', 'snapshot_as_of': '2026-01-01',
                'candidate': {'symbol': f'{600000 + i}.SH', 'industry': '银行', 'quant_score': 75},
                'excess_return': .1, 'case_id': f'case-{i}'} for i in range(20)]
    monkeypatch.setattr(api, 'replay_samples', lambda *_: samples)
    monkeypatch.setattr('tradingagents.core.memory_evaluation.load_strategy_lessons', lambda *_: [{
        'id': 'old', 'scope': 'industry', 'target': '银行', 'finding': '核对资产质量',
        'created_at': '2025-01-01', 'updated_at': '2025-01-01', 'active': True, 'governance_status': 'approved'}])
    calls = []

    class Reviewer:
        async def review(self, candidate, strategy_lessons):
            calls.append((candidate, strategy_lessons))
            context = current_context()
            context.budget.consume('model')
            db.save_agent_runtime({'run_id': str(uuid.uuid4()), 'root_id': context.root_id,
                'role': 'Candidate Reviewer', 'kind': 'model', 'provider': 'qianwen', 'model': 'qwen3.8-max',
                'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 10,
                'total_tokens': 110, 'cache_read_tokens': 20, 'cache_creation_tokens': 0, 'reasoning_tokens': 0}})
            return {'llm_view': 'positive' if strategy_lessons else 'neutral'}

    monkeypatch.setattr(api, 'build_candidate_reviewer', lambda *_args, **_kwargs: Reviewer())
    accepted = await api.start(request, api.EvaluationRequest())
    await app.state.memory_evaluation_jobs[accepted['id']]
    job = await api.status(accepted['id'], request)
    assert job['status'] == 'completed' and job['completed_pairs'] == 20
    assert len(calls) == 40 and all('excess_return' not in candidate for candidate, _ in calls)
    assert job['usage_stats']['input_tokens'] == 4000
    assert job['usage_stats']['output_tokens'] == 400
    assert job['usage_stats']['cache_read_tokens'] == 800
    assert job['usage_stats']['cost_cny'] > 0
    report = db.get_artifact(job['report_id'])['payload']
    assert report['rows'][0]['signal_snapshot']['quant_score'] == 75
    assert report['source_case_ids'] == [row['case_id'] for row in samples]
    assert db.list_reflection_cases() == []
    assert db.list_strategy_lessons() == []


@pytest.mark.asyncio
async def test_failed_pair_keeps_usage_without_automatic_retry(tmp_path, monkeypatch):
    from starlette.requests import Request

    from tradingagents.api.routes import memory_evaluation as api
    from tradingagents.core.agent_runtime import current_context

    db = Database(tmp_path / 'failed.db')
    app = FastAPI()
    app.state.db = db
    app.state.config = {'model_policy': {'default_model': 'offline-model'}, 'llm_provider': 'openai'}
    request = Request({'type': 'http', 'app': app})
    monkeypatch.setattr(api, 'replay_samples', lambda *_: [{'case_id': str(i)} for i in range(20)])

    async def fail(*_args, **_kwargs):
        root = current_context().root_id
        db.save_agent_runtime({'run_id': 'failed-call', 'root_id': root, 'role': 'Candidate Reviewer',
                               'kind': 'model', 'provider': 'openai', 'model': 'offline-model', 'status': 'failed'})
        raise RuntimeError('offline failure')

    monkeypatch.setattr(api, 'evaluate_memory_pairs', fail)
    accepted = await api.start(request, api.EvaluationRequest())
    await app.state.memory_evaluation_jobs[accepted['id']]
    job = await api.status(accepted['id'], request)
    assert job['status'] == 'failed' and job['usage_stats']['model_calls'] == 1
    assert job['usage_stats']['incomplete'] and job['usage_stats']['cost_cny'] is None
    assert db.list_artifacts(artifact_type='memory_evaluation') == []


def test_condition_learning_requires_original_evidence_quote(tmp_path):
    db = Database(tmp_path / 'lesson.db')
    engine = ReflectionEngine(db, {})
    case = {'id': 'case', 'symbol': '600000.SH', 'horizon_days': 60, 'eligible_for_strategy_learning': True,
            'snapshot_payload': {'style': 'medium_term', 'industry': '银行', 'decision_brief': brief(),
                                 'reasons': ['贷款质量数据缺失但未降级']}}
    attribution = {'attribution': 'ex_ante_miss', 'confidence': 'high', 'was_in_original_inputs': True,
                   'strategy_lesson': '数据缺失时需要核对安全边际'}
    assert engine.maybe_create_strategy_lesson(case, attribution) == {}
    lesson = engine.maybe_create_strategy_lesson(case, {**attribution, 'original_evidence_quotes': ['贷款质量数据缺失但未降级']})
    assert lesson['applicability'] == {'style': 'medium_term', 'horizon_days': 60}
    stored = db.get_strategy_lesson(lesson['id'])
    assert stored['governance_status'] == 'candidate' and not stored['active']


def test_condition_information_cutoff_excludes_past_hypothetical_entries():
    result = evaluate_decision_path(brief(info_cutoff='2026-01-03T13:00:00+08:00'), bars(10, 11, 12, 13, 14))
    assert result['entry_date'] == '2026-01-04'
