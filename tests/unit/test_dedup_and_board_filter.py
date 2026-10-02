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
    assert filters["min_amount_20d"] == 50_000


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


# ---------------------------------------------------------------------------
# industries hard restriction (chat “选5只半导体设备股” must not be dropped)
# ---------------------------------------------------------------------------


class TestRestrictRowsToIndustries:
    @staticmethod
    def _params(**kwargs):
        from tradingagents.skills.daily_pipeline.skill import DailyPipelineInput

        defaults = {"trade_date": "2026-07-24", "limit": 5, "candidate_limit": 80}
        defaults.update(kwargs)
        return DailyPipelineInput(**defaults)

    @staticmethod
    def _restrict(rows, params):
        from tradingagents.skills.daily_pipeline.skill import _restrict_rows_to_industries

        return _restrict_rows_to_industries(rows, params)

    def test_no_restriction_passthrough(self):
        rows = [{"ts_code": "600519.SH", "industry": "白酒"}]
        kept, warnings = self._restrict(rows, self._params())
        assert kept == rows
        assert warnings == []

    def test_bidirectional_substring_match(self):
        rows = [
            {"ts_code": "002371.SZ", "industry": "半导体"},          # row ⊂ keyword
            {"ts_code": "688012.SH", "industry": "半导体设备材料"},  # keyword ⊂ row
            {"ts_code": "600519.SH", "industry": "白酒"},
            {"ts_code": "000001.SZ"},  # no industry field → never matches
        ]
        kept, warnings = self._restrict(rows, self._params(industries=["半导体设备"]))
        assert [r["ts_code"] for r in kept] == ["002371.SZ", "688012.SH"]
        assert any("Industry restriction applied" in w for w in warnings)

    def test_sw_l1_expansion_matches_coarse_industry_field(self):
        """MCP industry 字段是申万一级（电子），细分词“半导体设备”必须能命中。"""
        rows = [
            {"ts_code": "002049.SZ", "industry": "电子"},
            {"ts_code": "600519.SH", "industry": "食品饮料"},
        ]
        kept, warnings = self._restrict(rows, self._params(industries=["半导体设备"]))
        assert [r["ts_code"] for r in kept] == ["002049.SZ"]
        assert any("半导体设备→电子" in w for w in warnings)

    def test_cxo_theme_maps_before_local_hard_filter(self):
        """Market 的 CXO 展示名必须映射为 MCP 的医药生物口径，不能筛成 0。"""
        rows = [
            {"ts_code": "603259.SH", "industry": "医药生物"},
            {"ts_code": "600519.SH", "industry": "食品饮料"},
        ]
        kept, warnings = self._restrict(rows, self._params(industries=["CXO"]))
        assert [row["ts_code"] for row in kept] == ["603259.SH"]
        assert any("CXO→医药生物" in warning for warning in warnings)

    def test_no_match_returns_empty_with_warning(self):
        rows = [{"ts_code": "600519.SH", "industry": "白酒"}]
        kept, warnings = self._restrict(rows, self._params(industries=["煤炭"]))
        assert kept == []
        assert any("No candidates matched" in w for w in warnings)

    def test_merged_sector_prefs_dedup_and_order(self):
        from tradingagents.skills.daily_pipeline.skill import _merged_sector_prefs

        params = self._params(industries=["半导体", "煤炭"])
        profile = {"sector_prefs": ["煤炭", "有色"]}
        # 半导体 expands to its SW L1 group 电子; 煤炭 has no synonym entry.
        assert _merged_sector_prefs(params, profile) == ["半导体", "电子", "煤炭", "有色"]

    def test_fetch_limit_maxed_when_industry_restricted(self):
        from tradingagents.skills.daily_pipeline.skill import _mcp_fetch_limit

        assert _mcp_fetch_limit(self._params()) == 40  # limit*8
        assert _mcp_fetch_limit(self._params(industries=["半导体"])) == 80  # full pool

    def test_industry_cap_skipped_when_industry_restricted(self):
        """行业限定下不再适用同行业分散帽，否则“选5只半导体”永远只能出2只。"""
        from tradingagents.skills.daily_pipeline.skill import _limit_rows_by_industry

        rows = [{"ts_code": f"60000{i}.SH", "industry": "电子"} for i in range(5)]
        kept, warnings = _limit_rows_by_industry(
            rows, self._params(industries=["半导体"]), {"daily_pipeline_max_per_industry": 2}
        )
        assert len(kept) == 5
        assert warnings == []
        # Without the restriction the cap still applies.
        capped, _ = _limit_rows_by_industry(
            rows, self._params(), {"daily_pipeline_max_per_industry": 2}
        )
        assert len(capped) == 2

    def test_input_schema_exposes_industries(self):
        """ChatAgent tool schema must include industries so the LLM can fill it."""
        from tradingagents.skills.daily_pipeline.skill import DailyPipelineInput

        schema = DailyPipelineInput.model_json_schema()
        assert "industries" in schema["properties"]
        assert "concepts" in schema["properties"]

    # -- SW L1 push-down to MCP filters.include_industries ----------------

    def test_mcp_include_industries_translates_to_sw_l1(self):
        from tradingagents.skills.daily_pipeline.skill import _mcp_include_industries

        # 细分词翻译到 L1；L1 名直接透传；去重保序。
        assert _mcp_include_industries(self._params(industries=["半导体"])) == ["电子"]
        assert _mcp_include_industries(self._params(industries=["煤炭"])) == ["煤炭"]
        assert _mcp_include_industries(
            self._params(industries=["芯片", "半导体", "白酒"])
        ) == ["电子", "食品饮料"]

    def test_mcp_include_industries_translates_investor_themes(self):
        from tradingagents.skills.daily_pipeline.skill import _mcp_include_industries

        assert _mcp_include_industries(self._params(industries=["CPO"])) == ["通信"]
        assert _mcp_include_industries(self._params(industries=["存储芯片"])) == ["电子"]
        assert _mcp_include_industries(self._params(industries=["创新药"])) == ["医药生物"]
        assert _mcp_include_industries(self._params(industries=["机器人"])) == ["机械设备"]
        assert _mcp_include_industries(self._params(industries=["CXO"])) == ["医药生物"]
        assert _mcp_include_industries(self._params(industries=["BC电池"])) == ["电力设备"]
        # Specific agriculture themes must win over the generic “生物” alias.
        assert _mcp_include_industries(self._params(industries=["生物育种"])) == ["农林牧渔"]

    def test_mcp_include_industries_empty_when_any_word_unresolvable(self):
        """部分翻译会在 MCP 精确匹配下误杀，必须整体回退本地过滤。"""
        from tradingagents.skills.daily_pipeline.skill import _mcp_include_industries

        assert _mcp_include_industries(self._params()) == []
        assert _mcp_include_industries(self._params(industries=["元宇宙"])) == []
        assert _mcp_include_industries(
            self._params(industries=["半导体", "元宇宙"])
        ) == []

    def test_daily_filters_injects_include_industries(self):
        from tradingagents.skills.daily_pipeline.skill import _daily_filters

        with_industry = _daily_filters(self._params(industries=["半导体"]), {})
        assert with_industry["include_industries"] == ["电子"]
        # 无行业限定 / 翻译失败时不能泄漏该 key。
        assert "include_industries" not in _daily_filters(self._params(), {})
        assert "include_industries" not in _daily_filters(
            self._params(industries=["元宇宙"]), {}
        )

    def test_daily_filters_prefers_standard_industry_codes_when_supported(self):
        from tradingagents.skills.daily_pipeline.skill import _daily_filters

        filters = _daily_filters(
            self._params(
                industries=["电子"],
                industry_taxonomy="CITICS",
                industry_level="L1",
                industry_codes=["CI005009.CI", "CI005009.CI"],
            ),
            {},
            taxonomy_supported=True,
        )
        assert filters["industry_taxonomy"] == "CITICS"
        assert filters["industry_level"] == "L1"
        assert filters["include_industry_codes"] == ["CI005009.CI"]
        assert "include_industries" not in filters

    def test_exact_concept_symbols_override_industry_proxy(self):
        from tradingagents.skills.daily_pipeline.skill import _daily_filters

        filters = _daily_filters(
            self._params(industries=["医药生物"], concepts=["CXO概念"]),
            {},
            include_symbols=["603259.SH", "300347.SZ", "603259.SH"],
        )
        assert filters["include_symbols"] == ["603259.SH", "300347.SZ"]
        assert "include_industries" not in filters

    def test_concept_constituents_resolve_atomically(self, monkeypatch):
        from tradingagents.dataflows import akshare_cn_specific as cn
        from tradingagents.skills.daily_pipeline.skill import _resolve_concept_symbol_filter

        monkeypatch.setattr(
            cn,
            "get_concept_constituents",
            lambda concept: {
                "source": "sina_concept_members",
                "rows": [
                    {"ts_code": "603259.SH"},
                    {"ts_code": "300347.SZ"},
                ],
            },
        )
        symbols, warnings = _resolve_concept_symbol_filter(
            self._params(industries=["医药生物"], concepts=["CXO概念"])
        )
        assert symbols == ["603259.SH", "300347.SZ"]
        assert any("2 current members" in warning for warning in warnings)

        monkeypatch.setattr(
            cn,
            "get_concept_constituents",
            lambda concept: {"source": None, "rows": []},
        )
        symbols, warnings = _resolve_concept_symbol_filter(
            self._params(industries=["医药生物"], concepts=["CXO概念"])
        )
        assert symbols == []
        assert any("fell back to MCP industry scope: 医药生物" in warning for warning in warnings)

    def test_synonym_groups_are_all_valid_sw_l1(self):
        """同义词表的映射产物必须全部是合法申万一级名，否则下发 MCP 会筛空。"""
        from tradingagents.skills.daily_pipeline.skill import (
            _INDUSTRY_L1_SYNONYMS,
            _SW_L1_NAMES,
        )

        invalid = {g for g in _INDUSTRY_L1_SYNONYMS.values() if g not in _SW_L1_NAMES}
        assert not invalid, f"non-SW-L1 groups in synonym table: {invalid}"

    # -- Fine-grained industry tier (TuShare 细分行业) ---------------------

    def test_fine_tier_replaces_pool_when_enough_matches(self):
        """细分命中数 >= limit 时，只保留细分行业行，丢弃同 L1 的其它行。"""
        rows = [
            {"ts_code": "600171.SH", "industry": "电子", "industry_detail": "半导体"},
            {"ts_code": "000050.SZ", "industry": "电子", "industry_detail": "元器件"},
            {"ts_code": "688012.SH", "industry": "电子", "industry_detail": "半导体"},
            {"ts_code": "002402.SZ", "industry": "电子", "industry_detail": "元器件"},
        ]
        kept, warnings = self._restrict(
            rows, self._params(industries=["半导体"], limit=2)
        )
        assert [r["ts_code"] for r in kept] == ["600171.SH", "688012.SH"]
        assert any("Fine-grained industry match" in w and "dropped 2" in w for w in warnings)

    def test_fine_tier_ranked_first_and_backfilled_when_insufficient(self):
        """细分命中不足 limit 时排在前面，用 L1 池补足。"""
        rows = [
            {"ts_code": "000050.SZ", "industry": "电子", "industry_detail": "元器件"},
            {"ts_code": "600171.SH", "industry": "电子", "industry_detail": "半导体"},
            {"ts_code": "002402.SZ", "industry": "电子", "industry_detail": "元器件"},
        ]
        kept, warnings = self._restrict(
            rows, self._params(industries=["半导体"], limit=3)
        )
        assert [r["ts_code"] for r in kept] == ["600171.SH", "000050.SZ", "002402.SZ"]
        assert any("backfilled from the SW L1 pool" in w for w in warnings)

    def test_fine_tier_degrades_to_coarse_without_detail(self):
        """无 industry_detail（TuShare 不可用）时保持既有 L1 行为。"""
        rows = [
            {"ts_code": "600171.SH", "industry": "电子"},
            {"ts_code": "000050.SZ", "industry": "电子"},
        ]
        kept, warnings = self._restrict(
            rows, self._params(industries=["半导体"], limit=2)
        )
        assert len(kept) == 2
        assert not any("Fine-grained" in w for w in warnings)

    def test_attach_fine_industry_annotates_rows(self, monkeypatch):
        import tradingagents.dataflows.tushare_common as tc
        from tradingagents.skills.daily_pipeline.skill import _attach_fine_industry

        monkeypatch.setattr(
            tc, "get_fine_industry_map", lambda: {"600171.SH": "半导体"}
        )
        rows = [{"ts_code": "600171.SH"}, {"ts_code": "600519.SH"}]
        _attach_fine_industry(rows)
        assert rows[0]["industry_detail"] == "半导体"
        assert "industry_detail" not in rows[1]

    def test_candidate_cards_pass_through_price_and_detail(self):
        """reviewed_candidates 卡片必须透传价格/交易计划与细分行业，
        否则前端 TradePlanBlock 渲染成“数据源缺失/未生成”。"""
        from tradingagents.skills.daily_pipeline.skill import _candidate_cards

        item = {
            "symbol": "600171.SH",
            "industry": "电子",
            "industry_detail": "半导体",
            "latest_price": 22.4,
            "close": 22.4,
            "price_trade_date": "2026-07-24",
            "price_source": "stockmanager_mcp",
            "entry_zone": [22.06, 22.62],
            "stop_loss": 20.61,
            "targets": [24.19, 25.98],
        }
        for include_llm in (False, True):
            card = _candidate_cards([item], include_llm=include_llm)[0]
            assert card["latest_price"] == 22.4
            assert card["entry_zone"] == [22.06, 22.62]
            assert card["stop_loss"] == 20.61
            assert card["targets"] == [24.19, 25.98]
            assert card["price_trade_date"] == "2026-07-24"
            assert card["industry_detail"] == "半导体"
