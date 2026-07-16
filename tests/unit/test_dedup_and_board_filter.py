"""Tests for reflection-case dedup + unified board_filter resolution."""



from tradingagents.core.persistence import Database
from tradingagents.skills._shared import (
    default_filters,
    resolve_board_filter,
)

# ---------------------------------------------------------------------------
# P1-1: case_id never falls back to uuid
# ---------------------------------------------------------------------------


def test_daily_pipeline_case_id_no_uuid_fallback():
    """case_id must always be derived from (trade_date, symbol), never uuid,
    so re-runs INSERT OR REPLACE instead of accumulating duplicates.

    case_id construction moved to the shared enroll_reflection_case helper
    during the A4 dedup refactor, so check the helper holds the invariant and
    daily_pipeline still routes through it."""
    import importlib
    import inspect

    enroll_module = importlib.import_module("tradingagents.core.reflection_enroll")
    helper_src = inspect.getsource(enroll_module.enroll_reflection_case)
    # The uuid fallback must be gone.
    assert "uuid" not in helper_src.lower(), "case_id still falls back to uuid"
    # A stable id is always built from source + date + symbol.
    assert "signal_date" in helper_src and "symbol" in helper_src

    # importlib.import_module returns the actual submodule from sys.modules,
    # even though the package __init__ shadows the name `skill` with an instance.
    dp_skill_module = importlib.import_module("tradingagents.skills.daily_pipeline.skill")
    dp_src = inspect.getsource(dp_skill_module._save_reflection_cases)
    assert "enroll_reflection_case" in dp_src, "daily_pipeline no longer uses shared helper"


# ---------------------------------------------------------------------------
# P1-2: historical duplicate cleanup
# ---------------------------------------------------------------------------


def test_dedup_reflection_cases_keeps_newest(tmp_path):
    db = Database(tmp_path / "t.db")
    # Insert 3 rows with the SAME (source_type, symbol, signal_date) but
    # different ids (simulating old run_id-embedded ids) and updated_at.
    rows = [
        ("daily_pipeline:runA:2026-07-04:600549.SH", "2026-07-04T07:00:00+00:00"),
        ("daily_pipeline:runB:2026-07-04:600549.SH", "2026-07-04T10:00:00+00:00"),
        ("daily_pipeline:runC:2026-07-04:600549.SH", "2026-07-04T12:00:00+00:00"),
    ]
    with db._conn() as conn:
        for cid, ts in rows:
            conn.execute(
                "INSERT INTO reflection_cases (id, source_type, reflection_scope, "
                "eligible_for_strategy_learning, status, symbol, name, signal_date, "
                "horizon_days, source_run_id, source_artifact_id, snapshot_payload, "
                "outcome_payload, post_signal_evidence_payload, attribution_payload, "
                "lesson_payload, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, "system_signal", "candidate_pool", 0, "pending",
                 "600549.SH", "x", "2026-07-04", 5, "", "", "{}",
                 "{}", "{}", "{}", "{}", ts, ts),
            )
        # A different symbol must survive.
        conn.execute(
            "INSERT INTO reflection_cases (id, source_type, reflection_scope, "
            "eligible_for_strategy_learning, status, symbol, name, signal_date, "
            "horizon_days, source_run_id, source_artifact_id, snapshot_payload, "
            "outcome_payload, post_signal_evidence_payload, attribution_payload, "
            "lesson_payload, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("daily_pipeline:2026-07-04:600276.SH", "system_signal", "candidate_pool",
             0, "pending", "600276.SH", "x", "2026-07-04", 5, "", "", "{}",
             "{}", "{}", "{}", "{}", "2026-07-04T07:00:00+00:00", "2026-07-04T07:00:00+00:00"),
        )
        db._dedup_reflection_cases(conn)
        remaining = conn.execute(
            "SELECT id, updated_at FROM reflection_cases ORDER BY symbol"
        ).fetchall()

    assert len(remaining) == 2
    # The kept 600549 row is the newest (runC, 12:00).
    kept_549 = [r for r in remaining if "600549" in r["id"]][0]
    assert "12:00:00" in kept_549["updated_at"]


def test_dedup_idempotent_on_clean_table(tmp_path):
    db = Database(tmp_path / "t.db")
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO reflection_cases (id, source_type, reflection_scope, "
            "eligible_for_strategy_learning, status, symbol, name, signal_date, "
            "horizon_days, source_run_id, source_artifact_id, snapshot_payload, "
            "outcome_payload, post_signal_evidence_payload, attribution_payload, "
            "lesson_payload, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("daily_pipeline:2026-07-04:600519.SH", "system_signal", "candidate_pool",
             0, "pending", "600519.SH", "x", "2026-07-04", 5, "", "", "{}",
             "{}", "{}", "{}", "{}", "2026-07-04T07:00:00+00:00", "2026-07-04T07:00:00+00:00"),
        )
        db._dedup_reflection_cases(conn)
        db._dedup_reflection_cases(conn)  # second run must not delete the survivor
        count = conn.execute("SELECT COUNT(*) FROM reflection_cases").fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# P2-1: resolve_board_filter priority chain
# ---------------------------------------------------------------------------


class TestResolveBoardFilter:
    def test_explicit_input_wins(self):
        assert resolve_board_filter("main_board", {}) == "main_board"
        assert resolve_board_filter("dual_growth_only", {"daily_pipeline_filters": {"board_filter": "main_board"}}) == "dual_growth_only"

    def test_filterpanel_falls_through_when_input_all(self):
        cfg = {"daily_pipeline_filters": {"board_filter": "main_board"}}
        assert resolve_board_filter("all", cfg) == "main_board"

    def test_filterpanel_empty_string_normalized_to_all(self):
        # FilterPanel default is "" — must not leak through as an invalid value.
        cfg = {"daily_pipeline_filters": {"board_filter": ""}}
        assert resolve_board_filter("all", cfg) == "all"

    def test_env_fallback_when_no_filterpanel(self):
        cfg = {"daily_pipeline_board_filter": "dual_growth_only"}
        assert resolve_board_filter("all", cfg) == "dual_growth_only"

    def test_filterpanel_preferred_over_env(self):
        cfg = {
            "daily_pipeline_filters": {"board_filter": "main_board"},
            "daily_pipeline_board_filter": "dual_growth_only",
        }
        assert resolve_board_filter("all", cfg) == "main_board"

    def test_default_all(self):
        assert resolve_board_filter("all", {}) == "all"
        assert resolve_board_filter(None, {}) == "all"
        assert resolve_board_filter("", {}) == "all"


def test_default_filters_normalizes_empty_board_filter():
    """An empty board_filter must not produce an invalid MCP filter key."""
    filters = default_filters(board_filter="", config={})
    assert "board_filter" not in filters  # "" treated as "all" → key omitted

    filters2 = default_filters(board_filter="main_board", config={})
    assert filters2["board_filter"]  # MCP-formatted value present


def test_default_filters_normalizes_filterpanel_board_filter():
    """A FilterPanel-saved empty board_filter must be normalized when merged."""
    filters = default_filters(
        board_filter="all",
        config={"daily_pipeline_filters": {"board_filter": "", "min_amount_20d": 5}},
    )
    assert filters.get("board_filter", "all") == "all" or filters.get("board_filter") is None or "board_filter" not in filters
    assert filters["min_amount_20d"] == 5


def test_apply_runtime_defaults_uses_filterpanel(tmp_path):
    """daily_pipeline._apply_runtime_defaults must pick up FilterPanel setting
    when the input board_filter is the default 'all'."""
    from tradingagents.skills.daily_pipeline.skill import (
        DailyPipelineInput,
        _apply_runtime_defaults,
    )

    cfg = {"daily_pipeline_filters": {"board_filter": "main_board"}}
    params = DailyPipelineInput(trade_date="2026-07-04", limit=5, candidate_limit=80)
    # Force trade_date match so only board_filter update is interesting.
    from tradingagents.core.trading_time import get_temporal_context

    ctx = get_temporal_context(cfg, market="cn_a")
    result = _apply_runtime_defaults(params, cfg, temporal_context=ctx)
    assert result.board_filter == "main_board"
