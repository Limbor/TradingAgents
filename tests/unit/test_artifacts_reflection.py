import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tradingagents.api.routes import artifacts, reflections
from tradingagents.core.persistence import Database
from tradingagents.core.reflection import ReflectionEngine
from tradingagents.skills.daily_pipeline.skill import _save_reflection_cases
from tradingagents.skills.stock_analysis.skill import (
    StockAnalysisInput,
    _format_holding_context,
    _load_holding_context,
)


def test_artifact_persistence_roundtrip(tmp_path):
    db = Database(tmp_path / "artifacts.db")
    db.save_artifact(
        artifact_id="a1",
        run_id="run-1",
        skill_id="daily_pipeline",
        artifact_type="decision_pack",
        title="每日选股",
        summary="BUY 1",
        payload={"decision_pack": [{"symbol": "600519.SH"}]},
        tags=["daily_pipeline", "cn_a"],
    )

    rows = db.list_artifacts(artifact_type="decision_pack")
    assert len(rows) == 1
    assert rows[0]["payload"]["decision_pack"][0]["symbol"] == "600519.SH"
    assert rows[0]["tags"] == ["daily_pipeline", "cn_a"]
    assert db.get_artifact("a1")["title"] == "每日选股"
    assert db.list_artifacts_for_run("run-1")[0]["id"] == "a1"


def test_save_report_syncs_stock_report_artifact(tmp_path):
    db = Database(tmp_path / "reports.db")
    db.save_report(
        report_id="r1",
        run_id="run-stock",
        ticker="600519.SH",
        ticker_name="贵州茅台",
        rating="Buy",
        content="# Report",
        path=None,
    )

    artifact = db.get_artifact("r1")
    assert artifact is not None
    assert artifact["artifact_type"] == "stock_report"
    assert artifact["subject_id"] == "600519.SH"
    assert artifact["content_markdown"] == "# Report"


def test_existing_reports_backfill_to_artifacts(tmp_path):
    db_path = tmp_path / "legacy.db"
    db = Database(db_path)
    with db._conn() as conn:
        conn.execute("DELETE FROM artifacts")
        conn.execute(
            """
            INSERT INTO reports (id, run_id, ticker, ticker_name, rating, content, report_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy-r1", "run-old", "600519.SH", "贵州茅台", "Buy", "# Legacy", None, "2026-07-01T00:00:00+00:00"),
        )

    reloaded = Database(db_path)
    artifact = reloaded.get_artifact("legacy-r1")
    assert artifact is not None
    assert artifact["artifact_type"] == "stock_report"
    assert artifact["content_markdown"] == "# Legacy"


def test_artifacts_api_filters_by_run(tmp_path):
    db = Database(tmp_path / "api.db")
    db.save_artifact(
        artifact_id="a1",
        run_id="run-1",
        skill_id="risk_monitor",
        artifact_type="risk_report",
        title="风险报告",
        payload={"risks": []},
    )
    app = FastAPI()
    app.state.db = db
    app.include_router(artifacts.router, prefix="/api/v1")

    client = TestClient(app)
    res = client.get("/api/v1/runs/run-1/artifacts")
    assert res.status_code == 200
    assert res.json()[0]["id"] == "a1"


def test_reflection_summary_contract(tmp_path):
    db = Database(tmp_path / "reflection-summary.db")
    db.save_reflection({
        "id": "r1",
        "ticker": "600519.SH",
        "trade_date": "2026-07-01",
        "original_decision": "BUY",
        "actual_return": 0.03,
        "was_correct": True,
        "reflection_text": "ok",
    })
    db.save_reflection({
        "id": "r2",
        "ticker": "000001.SZ",
        "trade_date": "2026-07-01",
        "original_decision": "BUY",
        "actual_return": -0.02,
        "was_correct": False,
        "reflection_text": "bad",
    })
    app = FastAPI()
    app.state.db = db
    app.include_router(reflections.router, prefix="/api/v1")

    payload = TestClient(app).get("/api/v1/reflections/summary").json()
    assert payload["total"] == 2
    assert payload["correct"] == 1
    assert payload["incorrect"] == 1
    assert payload["accuracy"] == 0.5


def test_reflection_case_and_strategy_lesson_roundtrip(tmp_path):
    db = Database(tmp_path / "reflection-case.db")
    db.save_reflection_case(
        case_id="case-1",
        source_type="system_signal",
        reflection_scope="decision_grade",
        eligible_for_strategy_learning=True,
        symbol="600519.SH",
        name="贵州茅台",
        signal_date="2026-07-01",
        snapshot_payload={"final_decision": "BUY", "data_coverage": {"flow": "missing"}},
    )
    case = db.list_reflection_cases(status="pending")[0]
    assert case["eligible_for_strategy_learning"] is True
    assert case["snapshot_payload"]["data_coverage"]["flow"] == "missing"

    db.save_strategy_lesson(
        lesson_id="lesson-1",
        lesson_type="signal_quality",
        scope="factor",
        target="flow",
        finding="资金流缺失时不要自动放大动量信号",
        suggested_adjustment="要求 LLM 降低催化剂置信度",
        confidence="medium",
    )
    lesson = db.list_strategy_lessons()[0]
    assert lesson["finding"].startswith("资金流缺失")
    assert lesson["active"] is True


def test_daily_pipeline_reflection_case_scopes(tmp_path):
    db = Database(tmp_path / "daily-case.db")
    result = _save_reflection_cases(
        db,
        "run-daily",
        "2026-07-01",
        [
            {"symbol": "600519.SH", "name": "贵州茅台", "final_decision": "BUY", "data_coverage": {"flow": "ok"}},
            {"symbol": "601899.SH", "name": "紫金矿业", "final_decision": "WATCHLIST", "data_coverage": {"flow": "missing"}},
        ],
    )
    assert result == {"created": 2, "failed": 0}
    cases = db.list_reflection_cases(limit=10)
    by_symbol = {case["symbol"]: case for case in cases}
    assert by_symbol["600519.SH"]["reflection_scope"] == "decision_grade"
    assert by_symbol["600519.SH"]["eligible_for_strategy_learning"] is True
    assert by_symbol["601899.SH"]["reflection_scope"] == "candidate_pool"
    assert by_symbol["601899.SH"]["eligible_for_strategy_learning"] is False


def test_reflection_engine_accepts_memory_log_date_field(tmp_path):
    class FakeMemory:
        def __init__(self):
            self.updated = None

        def get_pending_entries(self):
            return [{"date": "2026-07-01", "ticker": "600519.SH", "decision": "BUY", "pending": True}]

        def update_with_outcome(self, **kwargs):
            self.updated = kwargs

    class FakeEngine(ReflectionEngine):
        async def fetch_outcome(self, symbol, signal_date, horizon_days=5):
            return {"actual_return": 0.02, "horizon_days": horizon_days}

        async def generate_reflection(self, signal, outcome, evidence=""):
            return "good"

    async def run():
        memory = FakeMemory()
        db = Database(tmp_path / "reflection-engine.db")
        engine = FakeEngine(db=db, config={}, memory_log=memory)
        result = await engine.run_reflection_batch(lookback_days=9999)
        assert result["processed"] == 1
        assert memory.updated["trade_date"] == "2026-07-01"
        assert db.list_reflections()[0]["trade_date"] == "2026-07-01"
        assert db.list_artifacts(artifact_type="reflection_report")

    asyncio.run(run())


def test_reflection_engine_case_ex_ante_miss_creates_lesson(tmp_path):
    class FakeEngine(ReflectionEngine):
        async def fetch_outcome(self, symbol, signal_date, horizon_days=5):
            return {"actual_return": -0.05, "horizon_days": horizon_days}

        async def fetch_post_signal_evidence(self, symbol, signal_date, horizon_days=5):
            return {"announcements": [], "warnings": []}

        async def generate_attribution(self, case, outcome, post_signal_evidence):
            return {
                "attribution": "ex_ante_miss",
                "confidence": "medium",
                "was_in_original_inputs": True,
                "missed_evidence": ["flow missing"],
                "new_information": [],
                "strategy_lesson": "资金流缺失时不应维持强买入。",
                "risk_monitor_lesson": "",
                "suggested_adjustment": "降级缺少资金流确认的候选。",
            }

    async def run():
        db = Database(tmp_path / "case-reflection.db")
        db.save_reflection_case(
            case_id="case-1",
            source_type="system_signal",
            reflection_scope="decision_grade",
            eligible_for_strategy_learning=True,
            symbol="600519.SH",
            signal_date="2026-07-01",
            snapshot_payload={"final_decision": "BUY", "data_coverage": {"flow": "missing"}},
        )
        engine = FakeEngine(db=db, config={}, memory_log=None)
        result = await engine.run_reflection_batch(lookback_days=9999)
        assert result["cases_processed"] == 1
        assert result["lessons_created"] == 1
        assert db.list_reflection_cases(status="reflected")[0]["attribution_payload"]["attribution"] == "ex_ante_miss"
        assert db.list_strategy_lessons()[0]["finding"].startswith("资金流缺失")

    asyncio.run(run())


def test_stock_analysis_input_keeps_reflection_context():
    parsed = StockAnalysisInput.model_validate({
        "ticker": "600519.SH",
        "reflection_context": [{"trade_date": "2026-07-01", "reflection": "avoid chasing"}],
    })
    assert parsed.reflection_context[0]["reflection"] == "avoid chasing"


def test_stock_analysis_input_keeps_holding_context():
    parsed = StockAnalysisInput.model_validate({
        "ticker": "600519.SH",
        "holding_context": {"symbol": "600519.SH", "quantity": 10, "avg_cost": 1500},
    })
    assert parsed.holding_context["quantity"] == 10
    assert parsed.include_portfolio_context is True


def test_stock_analysis_loads_and_formats_holding_context(tmp_path):
    async def run():
        db = Database(tmp_path / "holding-context.db")
        db.upsert_holding("600519.SH", quantity=10, avg_cost=1500, current_price=1650, notes="核心仓")
        db.upsert_holding("601899.SH", quantity=100, avg_cost=18, current_price=20)

        context = await _load_holding_context(
            db,
            "600519.SH",
            {"stock_analysis_refresh_holding_price_context": False},
        )
        assert context is not None
        assert context["symbol"] == "600519.SH"
        assert context["unrealized_pnl"] == 1500
        assert context["unrealized_return"] == 0.1
        assert context["position_weight"] > 0

        rendered = _format_holding_context(context)
        assert "already held by the user" in rendered
        assert "unrealized P&L" in rendered
        assert "核心仓" in rendered

    asyncio.run(run())
