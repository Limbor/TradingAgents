"""Unit tests for the market_overview skill and its data-layer fallbacks."""

import asyncio
import importlib
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.agents.schemas import (
    IndustryStance,
    IndustryStanceList,
    TaggedNews,
    TaggedNewsList,
)
from tradingagents.core.industry_taxonomy import resolve_sw_l1_industries
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.skills.market_overview.skill import (
    MarketOverviewInput,
    _boards_to_rows,
    _build_readable_board_universe,
    _llm_industry_stances,
    _llm_tag_news,
    _render_report,
    _score_industry_rows,
    _select_boards_for_rating,
)

# The package __init__ re-exports the ``skill`` instance which shadows the
# ``skill`` submodule on attribute access, so resolve the module explicitly.
mo = importlib.import_module("tradingagents.skills.market_overview.skill")


def _board_rows(n: int) -> list[dict]:
    return [
        {"industry": f"板块{i}", "pct_change": float(i), "main_inflow": None, "leader_stock": None}
        for i in range(n)
    ]


class _FakeStructured:
    """Stand-in for a bind_structured() result."""

    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error

    def invoke(self, prompt):
        if self._error is not None:
            raise self._error
        return self._result


# ---------------------------------------------------------------------------
# Board selection: top-15 gainers + bottom-5 losers
# ---------------------------------------------------------------------------


def test_select_boards_top15_plus_bottom5():
    selected = _select_boards_for_rating(_board_rows(30), industry_top_n=20)
    names = [row["industry"] for row in selected]
    assert len(names) == 20
    # Top 15 by pct_change desc.
    assert names[:15] == [f"板块{i}" for i in range(29, 14, -1)]
    # Bottom 5 losers.
    assert set(names[15:]) == {"板块0", "板块1", "板块2", "板块3", "板块4"}


def test_select_boards_caps_at_top_n():
    selected = _select_boards_for_rating(_board_rows(30), industry_top_n=8)
    assert len(selected) == 8


def test_select_boards_dedups_overlap_and_skips_null_pct():
    rows = _board_rows(8)  # top-15 and bottom-5 slices overlap entirely
    rows.append({"industry": "无数据板块", "pct_change": None})
    selected = _select_boards_for_rating(rows, industry_top_n=20)
    names = [row["industry"] for row in selected]
    assert len(names) == len(set(names)) == 8
    assert "无数据板块" not in names


def test_select_boards_keeps_requested_focus_outside_top_bottom():
    rows = _board_rows(30)
    rows[12]["industry"] = "钨产业"
    selected = _select_boards_for_rating(rows, industry_top_n=20, focus_industries=["钨"])
    assert selected[0]["industry"] == "钨产业"


def test_readable_board_universe_uses_real_themes_plus_core_industries():
    concept_rows = [
        {"industry": "CPO概念", "pct_change": 4.2},
        {"industry": "存储芯片概念", "pct_change": 3.8},
        {"industry": "创新药概念", "pct_change": 2.1},
        {"industry": "机器人概念", "pct_change": 1.6},
        {"industry": "昨日涨停", "pct_change": 6.0},
    ]
    industry_rows = [
        {"industry": "半导体", "pct_change": 1.2},
        {"industry": "有色金属", "pct_change": -0.8},
        {"industry": "电子化学品III", "pct_change": 5.1},
        {"industry": "种子", "pct_change": 4.9},
    ]

    rows = _build_readable_board_universe(industry_rows, concept_rows, limit=6)
    by_name = {row["industry"]: row for row in rows}

    assert set(by_name) == {"CPO", "存储芯片", "创新药", "机器人", "半导体", "有色金属"}
    assert by_name["CPO"]["board_type"] == "concept"
    assert by_name["CPO"]["source_name"] == "CPO概念"
    assert by_name["CPO"]["selection_industries"] == ["通信"]
    assert by_name["CPO"]["selection_concept"] == "CPO概念"
    assert by_name["CPO"]["selection_mode"] == "proxy"
    assert by_name["半导体"]["board_type"] == "industry"
    assert "昨日涨停" not in by_name
    assert "电子化学品III" not in by_name


def test_readable_board_universe_preserves_standard_industry_identity():
    rows = _build_readable_board_universe(
        [{
            "industry": "电子",
            "source_name": "电子",
            "board_type": "industry",
            "taxonomy": "CITICS",
            "industry_code": "CI005009.CI",
            "industry_level": "L1",
            "pct_change": 1.25,
        }],
        [{"industry": "CPO概念", "pct_change": 2.0}],
        limit=50,
    )
    by_name = {row["industry"]: row for row in rows}

    assert by_name["电子"]["selection_taxonomy"] == "CITICS"
    assert by_name["电子"]["selection_level"] == "L1"
    assert by_name["电子"]["selection_industry_codes"] == ["CI005009.CI"]
    assert by_name["电子"]["selection_mode"] == "exact"


def test_citics_heat_keeps_only_requested_hierarchy(monkeypatch):
    from tradingagents.dataflows import tushare_common as tc

    daily = pd.DataFrame([
        {"ts_code": "CI005009.CI", "pct_change": 1.25, "amount": 100.0},
        {"ts_code": "CI005835.CI", "pct_change": 3.50, "amount": 200.0},
    ])
    fake_pro = SimpleNamespace(ci_daily=lambda **kwargs: daily)
    monkeypatch.setattr(tc, "get_pro_api", lambda: fake_pro)
    monkeypatch.setattr(tc, "tushare_call", lambda func, *args, **kwargs: func(*args, **kwargs))
    monkeypatch.setattr(
        tc,
        "_standard_industry_catalog",
        lambda taxonomy, level: {"CI005009.CI": "电子"},
    )

    rows = tc.get_standard_industry_heat(
        "2026-08-31",
        taxonomy="CITICS",
        level="L1",
    )

    assert rows == [{
        "industry": "电子",
        "source_name": "电子",
        "board_type": "industry",
        "taxonomy": "CITICS",
        "industry_code": "CI005009.CI",
        "industry_level": "L1",
        "pct_change": 1.25,
        "main_inflow": None,
        "leader_stock": None,
        "turnover_amount": 100.0,
    }]


def test_sw2021_heat_aggregates_official_members_when_index_daily_is_unavailable(monkeypatch):
    from tradingagents.dataflows import tushare_common as tc

    fake_pro = SimpleNamespace(
        daily=lambda **kwargs: pd.DataFrame([
            {"ts_code": "000001.SZ", "pct_chg": 1.0, "amount": 10.0},
            {"ts_code": "600000.SH", "pct_chg": 3.0, "amount": 20.0},
        ]),
        daily_basic=lambda **kwargs: pd.DataFrame([
            {"ts_code": "000001.SZ", "total_mv": 100.0},
            {"ts_code": "600000.SH", "total_mv": 300.0},
        ]),
    )
    monkeypatch.setattr(tc, "tushare_call", lambda func, *args, **kwargs: func(*args, **kwargs))
    monkeypatch.setattr(
        tc,
        "_sw2021_current_members",
        lambda pro: {
            "000001.SZ": ("801780.SI", "银行"),
            "600000.SH": ("801780.SI", "银行"),
        },
    )

    rows = tc._aggregate_sw2021_constituent_heat(
        fake_pro,
        "20260831",
        {"801780.SI": "银行"},
    )

    assert len(rows) == 1
    assert rows[0]["industry"] == "银行"
    assert rows[0]["industry_code"] == "801780.SI"
    assert rows[0]["pct_change"] == 2.5
    assert rows[0]["industry_return_method"] == "constituent_total_mv_weighted"
    assert rows[0]["constituent_count"] == 2


def test_readable_board_universe_exposes_cxo_mcp_selection_proxy():
    rows = _build_readable_board_universe(
        [],
        [
            {"industry": "CXO概念", "pct_change": 2.4},
            {"industry": "跨行业未映射概念", "pct_change": 1.0},
        ],
        limit=5,
    )
    by_name = {row["industry"]: row for row in rows}

    assert by_name["CXO"]["selection_industries"] == ["医药生物"]
    assert by_name["CXO"]["selection_concept"] == "CXO概念"
    assert by_name["CXO"]["selection_mode"] == "proxy"
    assert "跨行业未映射" not in by_name


def test_readable_board_universe_filters_event_labels_but_keeps_industry_chains():
    concept_rows = [
        {"industry": "未股改", "pct_change": 5.7},
        {"industry": "百度概念", "pct_change": 4.8},
        {"industry": "出口退税", "pct_change": 4.2},
        {"industry": "三沙概念", "pct_change": 3.9},
        {"industry": "内贸规划", "pct_change": 3.6},
        {"industry": "电解液概念", "pct_change": 3.1},
        {"industry": "碳纤维概念", "pct_change": 2.9},
        {"industry": "草甘膦概念", "pct_change": 2.5},
        {"industry": "建筑节能概念", "pct_change": 2.2},
        {"industry": "水产品概念", "pct_change": 1.9},
        {"industry": "猪肉概念", "pct_change": 1.8},
        {"industry": "聚氨酯概念", "pct_change": 1.7},
        {"industry": "石墨烯概念", "pct_change": 1.6},
        {"industry": "无线耳机概念", "pct_change": 1.5},
        {"industry": "智能电网概念", "pct_change": 1.4},
        {"industry": "钙钛矿概念", "pct_change": 1.3},
        {"industry": "充电桩概念", "pct_change": 1.2},
    ]

    rows = _build_readable_board_universe([], concept_rows, limit=50)
    by_name = {row["industry"]: row for row in rows}

    assert set(by_name) == {
        "电解液", "碳纤维", "草甘膦", "建筑节能", "水产品", "猪肉",
        "聚氨酯", "石墨烯", "无线耳机", "智能电网", "钙钛矿", "充电桩",
    }
    assert by_name["电解液"]["selection_industries"] == ["电力设备", "基础化工"]
    assert by_name["碳纤维"]["selection_industries"] == ["基础化工"]
    assert by_name["水产品"]["selection_industries"] == ["农林牧渔"]


def test_readable_board_universe_merges_harmony_synonyms_and_drops_generic_huawei():
    rows = _build_readable_board_universe(
        [],
        [
            {"industry": "鸿蒙概念", "pct_change": 3.1},
            {"industry": "华为鸿蒙概念", "pct_change": 2.6},
            {"industry": "华为概念", "pct_change": 2.0},
            {"industry": "华为海思概念", "pct_change": 1.8},
            {"industry": "华为汽车概念", "pct_change": 1.6},
        ],
        limit=50,
    )
    by_name = {row["industry"]: row for row in rows}

    assert set(by_name) == {"鸿蒙生态", "华为海思", "智能汽车"}
    assert by_name["鸿蒙生态"]["source_names"] == ["鸿蒙概念", "华为鸿蒙概念"]
    assert by_name["鸿蒙生态"]["selection_industries"] == ["计算机"]
    assert by_name["鸿蒙生态"]["selection_concept"] == "鸿蒙概念"
    assert by_name["智能汽车"]["selection_industries"] == ["汽车"]
    assert resolve_sw_l1_industries("华为") == []
    assert resolve_sw_l1_industries("华为鸿蒙") == ["计算机"]


def test_readable_board_universe_groups_legacy_industries_when_concepts_unavailable():
    industry_rows = [
        {"industry": "飞机制造", "pct_change": 3.0, "main_inflow": 10.0},
        {"industry": "船舶制造", "pct_change": 1.0, "main_inflow": 20.0},
        {"industry": "生物制药", "pct_change": 2.0, "main_inflow": 5.0},
        {"industry": "医疗器械", "pct_change": 4.0, "main_inflow": 6.0},
        {"industry": "次新股", "pct_change": 8.0},
    ]

    rows = _build_readable_board_universe(industry_rows, [], limit=50)
    by_name = {row["industry"]: row for row in rows}

    assert set(by_name) == {"国防军工", "医药生物"}
    assert by_name["国防军工"]["board_type"] == "industry_group"
    assert by_name["国防军工"]["pct_change"] == 2.0
    assert by_name["国防军工"]["main_inflow"] == 30.0
    assert by_name["国防军工"]["source_names"] == ["飞机制造", "船舶制造"]
    assert "次新股" not in by_name


def test_focused_report_explains_driver_and_sustainability():
    report = _render_report({
        "market_asof_date": "2026-08-05",
        "market_data": {},
        "regime": None,
        "focus_industries": ["钨"],
        "industry_stances": [{
            "industry": "钨产业",
            "pct_change": 4.2,
            "score": 72.0,
            "rating": "bullish",
            "rating_level": "strong_bullish",
            "confidence": "high",
            "phase": "趋势强化",
            "reason": "价格与资金共振",
            "evidence": ["日涨跌+4.20%", "主力净流入+3.0亿"],
        }],
        "news": [{
            "title": "钨矿供给收紧",
            "content": "行业供给约束延续",
            "industries": ["钨"],
            "interpretation": "供给侧催化得到新闻证据支持",
        }],
        "events": {},
    })
    assert report.startswith("## 钨板块驱动与持续性")
    assert "钨产业" in report
    assert "**持续性**：**中高**" in report
    assert "主力净流入" in report
    assert "条件式小仓试错（CONDITIONAL_BUY，初始仓位≤2%）" in report
    assert "### 明确交易建议" in report
    assert "### 行业观点" not in report
    assert "### 指数概览" not in report


def test_focused_report_labels_broad_industry_as_proxy():
    report = _render_report({
        "market_asof_date": "2026-08-05",
        "market_data": {"focus_concept_rows": []},
        "regime": None,
        "focus_industries": ["钨"],
        "industry_stances": [{
            "industry": "有色金属",
            "pct_change": 5.55,
            "score": 95.4,
            "rating": "bullish",
            "rating_level": "bullish",
            "confidence": "low",
            "phase": "领涨待确认",
            "reason": "日涨跌+5.55%",
            "evidence": ["日涨跌+5.55%", "成交额活跃度94分位"],
        }],
        "news": [],
        "events": {},
    })

    assert "相关行业代理证据" in report
    assert "不能等同于钨板块本身" in report
    assert "**持续性**：**待确认**" in report
    assert "暂不交易，不追涨（WAIT）" in report
    assert "偏多不等于已触发买点" in report
    assert "### 行业观点" not in report


def test_focused_report_without_evidence_explicitly_says_do_not_trade():
    report = _render_report({
        "market_asof_date": "2026-08-06",
        "market_data": {"focus_concept_rows": []},
        "regime": None,
        "focus_industries": ["钨"],
        "industry_stances": [],
        "news": [],
        "events": {},
    })

    assert "**当前动作：暂不交易（WAIT）**" in report
    assert "当前没有可验证的直接或代理行情证据" in report


# ---------------------------------------------------------------------------
# LLM industry stances: join back onto data rows + degradation
# ---------------------------------------------------------------------------


def test_industry_stances_join_back_on_rows():
    rows = _board_rows(20)
    stances = IndustryStanceList(
        stances=[
            IndustryStance(industry="板块19", rating="bullish", reason="资金流入", key_stocks=["龙头A"])
        ]
    )
    degraded: list[str] = []
    merged = _llm_industry_stances(_FakeStructured(stances), rows, 20, "2025-06-06", degraded)
    assert len(merged) == len(rows)  # all boards kept, not only the rated ones
    by_name = {row["industry"]: row for row in merged}
    assert by_name["板块19"]["rating"] == "bullish"
    assert by_name["板块19"]["ai_comment"] == "资金流入"
    assert by_name["板块19"]["key_stocks"] == ["龙头A"]
    assert by_name["板块0"]["rating"] in {"bearish", "neutral"}
    assert by_name["板块0"]["reason"] != by_name["板块19"]["reason"]
    assert degraded == []


def test_industry_stances_llm_failure_degrades():
    rows = _board_rows(10)
    degraded: list[str] = []
    merged = _llm_industry_stances(
        _FakeStructured(error=RuntimeError("boom")), rows, 20, "2025-06-06", degraded
    )
    assert "llm_industry_failed" in degraded
    assert len(merged) == len(rows)
    assert all(row["rating"] in {"bullish", "neutral", "bearish"} for row in merged)
    assert all(row["reason"] for row in merged)


def test_industry_stances_no_llm_degrades():
    degraded: list[str] = []
    merged = _llm_industry_stances(None, _board_rows(6), 20, "2025-06-06", degraded)
    assert "llm_industry_failed" in degraded
    assert all(row["rating"] in {"bullish", "neutral", "bearish"} for row in merged)


def test_industry_score_differentiates_without_fund_flow():
    rows = [
        {
            "industry": f"板块{i}",
            "pct_change": pct,
            "main_inflow": None,
            "turnover_amount": amount,
            "turnover_rate": None,
            "advance_count": None,
            "decline_count": None,
            "leader_stock": None,
        }
        for i, (pct, amount) in enumerate([(-4, 5), (-2, 4), (0, 3), (2, 4), (4, 5)])
    ]

    scored = _score_industry_rows(rows)
    assert scored[0]["rating"] == "bearish"
    assert scored[-1]["rating"] == "bullish"
    assert scored[2]["rating"] == "neutral"
    assert scored[0]["confidence"] == "low"
    assert "缺广度,资金数据" in scored[0]["reason"]
    assert len({row["reason"] for row in scored}) == len(scored)


def test_industry_score_uses_breadth_and_funds_for_high_confidence():
    rows = [
        {
            "industry": "强势行业",
            "pct_change": 4.0,
            "main_inflow": 8e8,
            "turnover_rate": 6.0,
            "advance_count": 18,
            "decline_count": 2,
            "leader_stock": "龙头A",
        },
        {
            "industry": "弱势行业",
            "pct_change": -3.0,
            "main_inflow": -6e8,
            "turnover_rate": 5.0,
            "advance_count": 2,
            "decline_count": 18,
            "leader_stock": "龙头B",
        },
        {
            "industry": "中间行业",
            "pct_change": 0.1,
            "main_inflow": 0.0,
            "turnover_rate": 2.0,
            "advance_count": 10,
            "decline_count": 10,
            "leader_stock": "龙头C",
        },
    ]

    by_name = {row["industry"]: row for row in _score_industry_rows(rows)}
    assert by_name["强势行业"]["rating_level"] == "strong_bullish"
    assert by_name["弱势行业"]["rating_level"] == "strong_bearish"
    assert by_name["强势行业"]["confidence"] == "high"
    assert by_name["强势行业"]["phase"] == "趋势强化"


def test_boards_to_rows_retains_available_scoring_dimensions():
    df = pd.DataFrame([
        {
            "板块名称": "半导体",
            "涨跌幅": 2.5,
            "主力净流入": 3e8,
            "换手率": 4.2,
            "总成交额": 9e9,
            "流通市值": 2e11,
            "总市值": 3e11,
            "上涨家数": 42,
            "下跌家数": 8,
            "领涨股票": "芯片龙头",
        }
    ])

    row = _boards_to_rows(df)[0]
    assert row["turnover_rate"] == 4.2
    assert row["turnover_amount"] == 9e9
    assert row["advance_count"] == 42
    assert row["decline_count"] == 8


# ---------------------------------------------------------------------------
# LLM news tagging degradation
# ---------------------------------------------------------------------------


def test_tag_news_without_llm_keeps_raw_items():
    news = [{"title": "新闻A", "content": "内容", "datetime": "2025-06-06 09:00"}]
    degraded: list[str] = []
    tagged = _llm_tag_news(None, news, 20, degraded)
    assert "llm_news_failed" in degraded
    assert tagged[0]["title"] == "新闻A"
    assert tagged[0]["polarity"] is None


def test_tag_news_empty_input():
    degraded: list[str] = []
    assert _llm_tag_news(None, [], 20, degraded) == []
    assert degraded == []


def _news_items(n: int) -> list[dict]:
    return [
        {"title": f"新闻{i}", "content": f"内容{i}", "datetime": "2025-06-06 09:00"}
        for i in range(n)
    ]


def _tagged(title: str) -> TaggedNews:
    return TaggedNews(
        title=title, polarity="bullish", impact_scope="market",
        impact_level="low", interpretation="解读",
    )


class _ChunkedStructured:
    """Fake structured LLM returning a queued result (or error) per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_tag_news_chunks_large_batches():
    news = _news_items(12)  # chunk size 8 → 2 calls
    fake = _ChunkedStructured([
        TaggedNewsList(items=[_tagged(f"新闻{i}") for i in range(8)]),
        TaggedNewsList(items=[_tagged(f"新闻{i}") for i in range(8, 12)]),
    ])
    degraded: list[str] = []
    tagged = _llm_tag_news(fake, news, 20, degraded)
    assert len(fake.prompts) == 2
    assert degraded == []
    assert len(tagged) == 12
    assert all(item["polarity"] == "bullish" for item in tagged)
    # Join-back keeps input order and raw timestamps.
    assert tagged[11]["title"] == "新闻11"
    assert tagged[11]["datetime"] == "2025-06-06 09:00"


def test_tag_news_failed_chunk_only_loses_its_items():
    news = _news_items(12)
    fake = _ChunkedStructured([
        RuntimeError("truncated output"),
        RuntimeError("truncated output again"),  # retry also fails
        TaggedNewsList(items=[_tagged(f"新闻{i}") for i in range(8, 12)]),
    ])
    degraded: list[str] = []
    tagged = _llm_tag_news(fake, news, 20, degraded)
    assert degraded == ["llm_news_failed"]
    assert len(tagged) == 12
    # First chunk untagged but items kept; second chunk fully tagged.
    assert all(tagged[i]["polarity"] is None for i in range(8))
    assert all(tagged[i]["polarity"] == "bullish" for i in range(8, 12))
    assert tagged[0]["title"] == "新闻0"


def test_tag_news_transient_failure_recovers_on_retry():
    news = _news_items(4)
    fake = _ChunkedStructured([
        RuntimeError("transient parse failure"),
        TaggedNewsList(items=[_tagged(f"新闻{i}") for i in range(4)]),
    ])
    degraded: list[str] = []
    tagged = _llm_tag_news(fake, news, 20, degraded)
    assert degraded == []
    assert len(fake.prompts) == 2  # first attempt + successful retry
    assert all(item["polarity"] == "bullish" for item in tagged)


# ---------------------------------------------------------------------------
# execute(): artifact saved with fixed daily id even when all LLM calls fail
# ---------------------------------------------------------------------------


def test_execute_saves_fixed_id_artifact_when_llm_unavailable(monkeypatch):
    saved: dict = {}

    def fake_save(config, **kwargs):
        saved.update(kwargs)

    def fake_fetch(trade_date, params, degraded):
        market_data = {
            "indices": [
                {
                    "code": "sh000001", "name": "上证指数", "close": 3000.0,
                    "pct_change": 0.5, "above_ma20": True, "ma20": 2990.0,
                    "closes_20d": [3000.0] * 20,
                }
            ],
            "breadth": {"up": 3000, "down": 2000, "flat": 300,
                        "limit_up": 40, "limit_down": 5, "broken_limit": 10},
            "northbound": None,
            "turnover_amount": 1.0e12,
            "macro_tail": [],
        }
        return market_data, _board_rows(6), [
            {"title": "新闻A", "content": "内容", "datetime": "2025-06-06 09:00"}
        ], [], []

    monkeypatch.setattr(mo, "save_skill_artifact", fake_save)
    monkeypatch.setattr(mo, "_fetch_market_data", fake_fetch)
    monkeypatch.setattr(mo, "_make_structured_llms", lambda config, degraded: {})

    async def run():
        return [
            event
            async for event in mo.skill.execute(MarketOverviewInput(), dict(DEFAULT_CONFIG))
        ]

    events = asyncio.run(run())

    payload = saved["payload"]
    asof = payload["market_asof_date"]
    assert saved["artifact_id"] == f"market-overview-cn_a-{asof}"
    assert saved["artifact_type"] == "market_overview"
    assert saved["subject_id"] == "cn_a"
    # LLM degraded but the data snapshot is still persisted.
    assert payload["regime"] is None
    assert {"llm_industry_failed", "llm_news_failed"} <= set(payload["degraded"])
    assert payload["news"][0]["title"] == "新闻A"
    assert payload["news"][0]["polarity"] is None
    assert payload["market_data"]["breadth"]["limit_up"] == 40

    complete = [e for e in events if e.event_type == "skill_complete"][-1]
    assert complete.data["status"] == "success"
    stage_ids = {
        e.data.get("stage_id") for e in events if e.event_type == "skill_progress"
    }
    assert {"fetch_data", "llm_regime", "llm_industry", "llm_news", "save"} <= stage_ids

    # A focused question is a separate sector artifact and must not overwrite
    # the fixed whole-market snapshot used by /market.
    async def run_focused():
        return [
            event
            async for event in mo.skill.execute(
                MarketOverviewInput(focus_industries=["钨"]),
                dict(DEFAULT_CONFIG),
            )
        ]

    focused_events = asyncio.run(run_focused())
    assert focused_events
    assert saved["artifact_id"] == f"sector-analysis-钨-{saved['payload']['market_asof_date']}"
    assert saved["artifact_type"] == "sector_analysis"
    assert saved["subject_type"] == "industry"
    assert saved["content_markdown"].startswith("## 钨板块驱动与持续性")


def test_execute_rejects_empty_market_snapshot(monkeypatch):
    """A total provider outage must fail instead of overwriting the last good artifact."""
    monkeypatch.setattr(
        mo,
        "_fetch_market_data",
        lambda trade_date, params, degraded: (
            {
                "indices": [],
                "breadth": None,
                "northbound": None,
                "turnover_amount": None,
                "macro_tail": [],
            },
            [],
            [],
            [],
            [],
        ),
    )
    monkeypatch.setattr(
        mo,
        "save_skill_artifact",
        lambda *args, **kwargs: pytest.fail("empty snapshot must not be saved"),
    )

    async def run():
        return [
            event
            async for event in mo.skill.execute(MarketOverviewInput(), dict(DEFAULT_CONFIG))
        ]

    with pytest.raises(RuntimeError, match="核心数据全部不可用"):
        asyncio.run(run())


def test_execute_times_out_market_fetch(monkeypatch):
    real_to_thread = asyncio.to_thread

    async def fake_to_thread(func, *args, **kwargs):
        if func is mo._fetch_market_data:
            await asyncio.sleep(60)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(mo.asyncio, "to_thread", fake_to_thread)

    async def run():
        config = {**DEFAULT_CONFIG, "market_overview_fetch_timeout_seconds": 0.01}
        return [
            event async for event in mo.skill.execute(MarketOverviewInput(), config)
        ]

    with pytest.raises(RuntimeError, match="市场数据抓取超过"):
        asyncio.run(run())


def test_execute_degrades_when_industry_llm_times_out(monkeypatch):
    saved: dict = {}
    real_industry = mo._llm_industry_stances

    def fake_fetch(trade_date, params, degraded):
        market_data = {
            "indices": [{"code": "sh000001", "name": "上证指数", "close": 3000.0}],
            "breadth": {"up": 3000, "down": 2000},
            "northbound": None,
            "turnover_amount": 1.0e12,
            "macro_tail": [],
        }
        return market_data, _board_rows(6), [{"title": "新闻A"}], [], []

    class SlowIndustry:
        pass

    def slow_industry(structured_llm, *args):
        if isinstance(structured_llm, SlowIndustry):
            import time

            time.sleep(0.1)
        return real_industry(None, *args)

    monkeypatch.setattr(mo, "_fetch_market_data", fake_fetch)
    monkeypatch.setattr(mo, "save_skill_artifact", lambda config, **kwargs: saved.update(kwargs))
    monkeypatch.setattr(
        mo,
        "_make_structured_llms",
        lambda config, degraded: {"industry": SlowIndustry()},
    )
    monkeypatch.setattr(mo, "_llm_industry_stances", slow_industry)

    async def run():
        config = {**DEFAULT_CONFIG, "market_overview_llm_stage_timeout_seconds": 0.01}
        return [
            event async for event in mo.skill.execute(MarketOverviewInput(), config)
        ]

    events = asyncio.run(run())

    assert any(event.event_type == "skill_complete" for event in events)
    assert "llm_industry_timeout" in saved["payload"]["degraded"]
    assert len(saved["payload"]["industry_stances"]) == 6


# ---------------------------------------------------------------------------
# Data-layer fallback branches (mocked AKShare)
# ---------------------------------------------------------------------------


def test_market_breadth_falls_back_to_limit_pools(monkeypatch):
    from tradingagents.dataflows import akshare_cn_specific as mod

    fake_ak = SimpleNamespace(
        stock_market_activity_legu=object(),
        stock_zt_pool_em=object(),
        stock_zt_pool_dtgc_em=object(),
        stock_zt_pool_zbgc_em=object(),
    )

    def fake_call(fn, *args, **kwargs):
        if fn is fake_ak.stock_market_activity_legu:
            raise RuntimeError("legu down")
        if fn is fake_ak.stock_zt_pool_em:
            return pd.DataFrame({"代码": ["000001"] * 30})
        if fn is fake_ak.stock_zt_pool_dtgc_em:
            return pd.DataFrame({"代码": ["000002"] * 4})
        if fn is fake_ak.stock_zt_pool_zbgc_em:
            return pd.DataFrame({"代码": ["000003"] * 7})
        raise AssertionError("unexpected akshare call")

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_call)

    breadth = mod.get_market_breadth("2025-06-06")
    assert breadth["limit_up"] == 30
    assert breadth["limit_down"] == 4
    assert breadth["broken_limit"] == 7
    assert breadth["up"] is None and breadth["down"] is None


def test_market_news_flash_falls_back_to_cctv(monkeypatch):
    from tradingagents.dataflows import akshare_news as mod

    fake_ak = SimpleNamespace(stock_info_global_cls=object(), news_cctv=object())

    def fake_call(fn, *args, **kwargs):
        if fn is fake_ak.stock_info_global_cls:
            raise RuntimeError("cls down")
        if fn is fake_ak.news_cctv:
            return pd.DataFrame({"title": ["联播要闻"], "content": ["经济稳中向好"]})
        raise AssertionError("unexpected akshare call")

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_call)

    items = mod.get_market_news_flash("2025-06-06", limit=10)
    # Walks back 3 days of CCTV scripts, 1 item per day.
    assert len(items) == 3
    assert items[0]["title"] == "联播要闻"
    assert items[0]["content"] == "经济稳中向好"


def test_market_indices_overview_computes_ma20_and_turnover(monkeypatch):
    from tradingagents.dataflows import akshare_cn_specific as mod

    fake_ak = SimpleNamespace(stock_zh_index_daily_em=object())
    dates = pd.date_range(end="2025-06-06", periods=30, freq="D")

    def fake_call(fn, *args, **kwargs):
        assert fn is fake_ak.stock_zh_index_daily_em
        closes = [100.0 + i for i in range(30)]
        return pd.DataFrame({
            "date": dates,
            "close": closes,
            "amount": [1_000_000.0] * 30,
        })

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_call)

    result = mod.get_market_indices_overview("2025-06-06")
    assert len(result["indices"]) == 3
    entry = result["indices"][0]
    assert entry["close"] == 129.0
    assert len(entry["closes_20d"]) == 20
    assert entry["above_ma20"] is True
    # SSE + SZSE amounts only.
    assert result["turnover_amount"] == 2_000_000.0


def test_index_overview_falls_back_to_sina(monkeypatch):
    from tradingagents.dataflows import akshare_cn_specific as mod

    dates = pd.date_range("2025-05-01", periods=30).strftime("%Y-%m-%d").tolist()
    fake_ak = SimpleNamespace(
        stock_zh_index_daily_em=object(),
        stock_zh_index_daily=object(),
    )

    def fake_call(fn, *args, **kwargs):
        if fn is fake_ak.stock_zh_index_daily_em:
            raise RuntimeError("eastmoney blocked")
        assert fn is fake_ak.stock_zh_index_daily
        # Sina daily bars carry no amount column.
        return pd.DataFrame({
            "date": dates,
            "close": [100.0 + i for i in range(30)],
        })

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_call)
    monkeypatch.setattr(mod, "_tushare_index_daily", lambda *a, **k: None)

    result = mod.get_market_indices_overview("2025-06-06")
    assert len(result["indices"]) == 3
    assert result["indices"][0]["close"] == 129.0
    # No amount column from Sina, so turnover stays unavailable.
    assert result["turnover_amount"] is None


def test_index_overview_falls_back_to_tushare(monkeypatch):
    import tradingagents.dataflows.tushare_common as tc
    from tradingagents.dataflows import akshare_cn_specific as mod

    fake_ak = SimpleNamespace(stock_zh_index_daily_em=object(), stock_zh_index_daily=object())
    fake_pro = SimpleNamespace(index_daily=object())

    def fake_ak_call(fn, *args, **kwargs):
        raise RuntimeError("eastmoney blocked")

    def fake_ts_call(fn, *args, **kwargs):
        assert fn is fake_pro.index_daily
        dates = pd.date_range("2025-05-01", periods=30).strftime("%Y%m%d").tolist()
        # TuShare returns rows in descending date order, amount in 千元.
        return pd.DataFrame({
            "trade_date": list(reversed(dates)),
            "close": [129.0 - i for i in range(30)],
            "amount": [1_000.0] * 30,
        })

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_ak_call)
    monkeypatch.setattr(tc, "get_pro_api", lambda: fake_pro)
    monkeypatch.setattr(tc, "tushare_call", fake_ts_call)

    result = mod.get_market_indices_overview("2025-06-06")
    assert len(result["indices"]) == 3
    assert result["indices"][0]["close"] == 129.0
    # 千元 → 元 conversion, SSE + SZSE amounts only.
    assert result["turnover_amount"] == 2_000_000.0


def test_unlock_overview_falls_back_to_tushare(monkeypatch):
    import tradingagents.dataflows.tushare_common as tc
    from tradingagents.dataflows import akshare_cn_specific as mod

    fake_ak = SimpleNamespace(stock_restricted_release_detail_em=object())
    fake_pro = SimpleNamespace(share_float=object(), stock_basic=object())

    def fake_ak_call(fn, *args, **kwargs):
        raise RuntimeError("eastmoney blocked")

    def fake_ts_call(fn, *args, **kwargs):
        if fn is fake_pro.share_float:
            # Two shareholder rows for the same stock/date get aggregated.
            return pd.DataFrame({
                "ts_code": ["301121.SZ", "301121.SZ", "600519.SH"],
                "float_date": ["20250610", "20250610", "20250609"],
                "float_share": [1000.0, 2000.0, 500.0],
                "float_ratio": [1.5, 2.5, 0.1],
            })
        assert fn is fake_pro.stock_basic
        return pd.DataFrame({
            "ts_code": ["301121.SZ", "600519.SH"],
            "name": ["海力风电", "贵州茅台"],
        })

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_ak_call)
    monkeypatch.setattr(tc, "get_pro_api", lambda: fake_pro)
    monkeypatch.setattr(tc, "tushare_call", fake_ts_call)

    events = mod.get_market_unlock_overview("2025-06-06", days_ahead=14)
    assert events == [
        {"symbol": "600519", "name": "贵州茅台", "date": "2025-06-09", "market_value": None},
        {"symbol": "301121", "name": "海力风电", "date": "2025-06-10", "market_value": None},
    ]


def test_board_heat_falls_back_to_sina(monkeypatch):
    from tradingagents.dataflows import akshare_cn_specific as mod

    fake_ak = SimpleNamespace(
        stock_board_industry_name_em=object(),
        stock_sector_spot=object(),
    )

    def fake_call(fn, *args, **kwargs):
        if fn is fake_ak.stock_board_industry_name_em:
            raise RuntimeError("eastmoney blocked")
        assert fn is fake_ak.stock_sector_spot
        assert kwargs.get("indicator") == "新浪行业"
        return pd.DataFrame({
            "label": ["new_a", "new_b"],
            "板块": ["玻璃行业", "船舶制造"],
            "涨跌幅": [-2.7, 1.5],
            "总成交额": [9906106545, 4513201477],
            "股票名称": ["德力股份", "ST亚光"],
        })

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_call)
    monkeypatch.setattr(mod, "_eastmoney_board_heat_fallback", lambda *a, **k: None)

    df = mod.get_board_heat("2025-06-06", board_type="industry")
    assert df is not None
    assert list(df["板块名称"]) == ["船舶制造", "玻璃行业"]  # sorted by 涨跌幅 desc
    assert df["source"].iloc[0] == "sina_sector_spot"
    # Normalized columns feed the skill's row extractor correctly.
    rows = mo._boards_to_rows(df)
    assert rows[0]["industry"] == "船舶制造"
    assert rows[0]["pct_change"] == 1.5
    assert rows[0]["main_inflow"] is None
    assert rows[0]["leader_stock"] == "ST亚光"
    assert rows[0]["turnover_amount"] == 4513201477


def test_concept_board_heat_falls_back_to_sina(monkeypatch):
    from tradingagents.dataflows import akshare_cn_specific as mod

    fake_ak = SimpleNamespace(
        stock_board_concept_name_em=object(),
        stock_sector_spot=object(),
    )

    def fake_call(fn, *args, **kwargs):
        if fn is fake_ak.stock_board_concept_name_em:
            raise RuntimeError("eastmoney blocked")
        assert fn is fake_ak.stock_sector_spot
        assert kwargs.get("indicator") == "概念"
        return pd.DataFrame({
            "板块": ["CPO概念", "创新药"],
            "涨跌幅": [3.2, -1.5],
            "总成交额": [10_000_000, 20_000_000],
            "股票名称": ["光模块龙头", "医药龙头"],
        })

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_call)
    monkeypatch.setattr(mod, "_eastmoney_board_heat_fallback", lambda *a, **k: None)

    df = mod.get_board_heat("2026-08-17", board_type="concept")

    assert df is not None
    assert list(df["板块名称"]) == ["CPO概念", "创新药"]
    assert df["类型"].iloc[0] == "概念板块"
    assert df["source"].iloc[0] == "sina_concept_spot"


def test_macro_snapshot_skips_unpublished_tail_rows(monkeypatch):
    from tradingagents.dataflows import akshare_macro as mod

    fake_ak = SimpleNamespace(macro_china_ppi_yearly=object())

    def fake_call(fn, *args, **kwargs):
        assert fn is fake_ak.macro_china_ppi_yearly
        # Calendar-style series: the last row is a scheduled release with
        # no published reading yet.
        return pd.DataFrame({
            "日期": ["2025-07-09", "2025-08-09", "2025-09-10"],
            "今值": [-3.6, -2.9, None],
        })

    monkeypatch.setattr(mod, "ak_lazy_import", lambda: fake_ak)
    monkeypatch.setattr(mod, "akshare_call", fake_call)

    items = mod.get_macro_snapshot("2025-09-15")
    ppi = next(item for item in items if item["name"] == "PPI 同比")
    assert ppi["value"] == -2.9
    assert ppi["date"] == "2025-08-09"
