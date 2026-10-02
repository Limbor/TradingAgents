"""Task-specific memory retrieval, historical cutoffs and injection receipts."""

import json
from types import SimpleNamespace

import pytest

from tradingagents.core.candidate_review_runner import apply_llm_reviews, candidate_lesson_hits
from tradingagents.core.lightweight_tools import make_get_strategy_lessons
from tradingagents.core.llm_candidate_review import CandidateReviewer
from tradingagents.core.strategy_memory import select_strategy_lessons, validate_memory_usage


def _lesson(lesson_id="industry", **kwargs):
    return {"id": lesson_id, "scope": "industry", "target": "地产",
            "finding": f"{lesson_id} finding", "suggested_adjustment": "检查量价持续性",
            "confidence": "medium", "evidence_count": 5, "active": True,
            "governance_status": "approved", "created_at": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-06-02T00:00:00+00:00", **kwargs}


def test_retrieval_ranks_specificity_and_normalizes_symbols_and_industries():
    lessons = [_lesson("global", scope="global", evidence_count=100),
               _lesson("industry"), _lesson("symbol", scope="symbol", target="000002.SS"),
               _lesson("unrelated", target="医药"),
               _lesson("factor", scope="factor", target="funds")]
    context = {"symbol": "000002.SH", "industry": "房地产开发",
               "data_coverage": {"funds": "missing"}}
    selected = select_strategy_lessons(lessons, context, as_of_date="2026-07-01")
    assert [row["id"] for row in selected] == ["symbol", "industry", "factor", "global"]
    assert [row["id"] for row in candidate_lesson_hits(context, lessons)] == [row["id"] for row in selected]


@pytest.mark.parametrize("changes", [
    {"active": False}, {"governance_status": "candidate"}, {"governance_status": "retired"},
    {"expires_at": "2026-06-30T00:00:00Z"}, {"expires_at": "invalid"},
    {"created_at": "2026-07-02"}, {"updated_at": "2026-07-02"},
    {"created_at": None}, {"updated_at": "invalid", "created_at": None},
    {"updated_at": "invalid"},
    {"payload": {"applicability": {"style": "short_term"}}},
    {"payload": {"applicability": {"regime": "bullish"}}},
])
def test_retrieval_excludes_unusable_or_future_lessons(changes):
    context = {"industry": "房地产", "style": "medium_term"}
    assert select_strategy_lessons([_lesson(**changes)], context, as_of_date="2026-07-01") == []


def test_cutoff_uses_beijing_day_and_preserves_applicability_in_snapshots():
    lesson = _lesson(updated_at="2026-07-01T16:00:00Z",
                     payload={"applicability": {"style": "medium_term"}})
    context = {"industry": "房地产", "style": "medium_term"}
    assert select_strategy_lessons([lesson], context, as_of_date="2026-07-01") == []
    selected = select_strategy_lessons([lesson], context, as_of_date="2026-07-02")
    assert selected
    assert select_strategy_lessons(selected, {**context, "style": "short_term"}, as_of_date="2026-07-02") == []
    assert select_strategy_lessons([lesson], context, as_of_date="bad") == []


@pytest.mark.asyncio
async def test_tool_ranks_beyond_recent_twenty_and_returns_bounded_snapshots():
    calls = []
    lessons = [_lesson(f"unrelated-{index}", target="医药") for index in range(30)]
    lessons.extend([_lesson("old-relevant", finding="a" * 4000), _lesson("global", scope="global")])

    def list_lessons(**kwargs):
        calls.append(kwargs)
        return lessons

    result = await make_get_strategy_lessons(SimpleNamespace(list_strategy_lessons=list_lessons))(
        industries=["房地产"], as_of_date="2026-07-01", limit=1,
    )
    assert calls == [{"limit": 2000, "active_only": True}]
    assert [row["id"] for row in result["lessons"]] == ["old-relevant"]
    assert len(result["lessons"][0]["finding"]) == 500
    assert result["kind"] == "historical_memory"
    assert "as_of_date" not in result  # Memory cutoff is not a market date.


@pytest.mark.asyncio
async def test_actual_prompt_and_injection_receipt_have_identical_lessons():
    prompts = []
    lessons = [_lesson(f"global-{i}", scope="global") for i in range(7)]
    lessons += [_lesson("industry"), _lesson("future", updated_at="2026-07-03")]

    class Model:
        def invoke(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(content=json.dumps({
                "llm_view": "neutral", "reasoning": "等待数据确认", "memory_usage": [
                    {"lesson_id": "industry", "status": "referenced", "reason": "行业场景相同"},
                    {"lesson_id": "future", "status": "referenced", "reason": "不能引用"},
                ],
            }))

    candidate = {"symbol": "000002.SZ", "industry": "房地产开发", "quant_score": 70,
                 "factor_scores": {}, "risk_flags": []}
    reviewer = CandidateReviewer(Model(), style="medium_term", trade_date="2026-07-01",
                                 strategy_lessons=lessons)
    await apply_llm_reviews([candidate], config={}, trade_date="2026-07-01", style="medium_term",
                            reviewer=reviewer, strategy_lessons=lessons)
    trace = candidate["memory_trace"]
    assert trace["status"] == "injected"
    assert len(trace["injected_ids"]) == 5
    assert trace["injected_ids"][0] == "industry"
    injected_json = json.loads(prompts[0].split("## 历史经验参考", 1)[1].split("\n", 2)[2].split("\n\n", 1)[0])
    assert [row["id"] for row in injected_json] == trace["injected_ids"]
    assert "future finding" not in prompts[0]
    assert trace["usage"] == [{"lesson_id": "industry", "status": "referenced", "reason": "行业场景相同"}]


@pytest.mark.asyncio
async def test_custom_reviewer_without_memory_input_does_not_claim_injection():
    class Reviewer:
        async def review(self, _candidate):
            return {"reasoning": "普通复核", "memory_usage": [
                {"lesson_id": "industry", "status": "referenced", "reason": "声称使用"},
            ]}

    candidate = {"symbol": "000002.SZ", "industry": "房地产", "quant_score": 70}
    await apply_llm_reviews([candidate], config={}, trade_date="2026-07-01", style="medium_term",
                            reviewer=Reviewer(), strategy_lessons=[_lesson()])
    assert candidate["memory_trace"]["retrieved_ids"] == ["industry"]
    assert candidate["memory_trace"]["injected_ids"] == []
    assert candidate["memory_trace"]["usage"] == []
    assert "lesson_adjustment_reason" not in candidate


def test_memory_usage_rejects_foreign_ids_duplicates_and_invalid_statuses():
    selected = [_lesson()]
    usage = [{"lesson_id": "industry", "status": "not_applicable", "reason": "持有周期不同"},
             {"lesson_id": "industry", "status": "referenced", "reason": "重复"},
             {"lesson_id": "other", "status": "referenced", "reason": "本轮没收到"}]
    assert validate_memory_usage(usage, selected) == usage[:1]


@pytest.mark.parametrize("batched", [True, False])
def test_legacy_log_records_when_reflection_became_available(tmp_path, batched):
    from tradingagents.agents.utils.memory import TradingMemoryLog

    path = tmp_path / "memory.md"
    log = TradingMemoryLog({"memory_log_path": str(path)})
    log.store_decision("000002.SZ", "2026-01-01", "Rating: Hold")
    if batched:
        log.batch_update_with_outcomes([{
            "ticker": "000002.SZ", "trade_date": "2026-01-01", "raw_return": 0.01,
            "alpha_return": 0.001, "holding_days": 5, "reflection": "观察量价改善",
        }])
    else:
        log.update_with_outcome("000002.SZ", "2026-01-01", 0.01, 0.001, 5, "观察量价改善")
    entry = log.load_entries()[0]
    assert entry["available_at"] is not None
    assert entry["reflection"] == "观察量价改善"
    assert log.get_past_context("000002.SZ", as_of_date="2026-01-01") == ""
    assert "观察量价改善" in log.get_past_context("000002.SZ", as_of_date="2099-01-01")


def test_undated_legacy_memory_is_not_used_in_historical_analysis(tmp_path):
    from tradingagents.agents.utils.memory import TradingMemoryLog

    path = tmp_path / "memory.md"
    path.write_text("[2026-01-01 | 000002.SZ | Hold | +1% | +0% | 5d]\n\n"
                    "DECISION:\nRating: Hold\n\nREFLECTION:\n旧经验" + TradingMemoryLog._SEPARATOR)
    log = TradingMemoryLog({"memory_log_path": str(path)})
    assert "旧经验" in log.get_past_context("000002.SZ")  # Old files remain readable.
    assert log.get_past_context("000002.SZ", as_of_date="2026-07-01") == ""
