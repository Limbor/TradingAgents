"""Tests for the shared candidate LLM review runner.

Covers the behaviour extracted from daily_pipeline and now also used by
market_scanner: available reviewer re-fuses candidates, review_limit
truncation, *visible* degradation when the reviewer is unavailable, the
disabled/empty paths, and per-candidate review failures.
"""

import asyncio

from tradingagents.core.candidate_review_runner import (
    apply_llm_reviews,
    candidate_lesson_hits,
    coerce_llm_review,
)


class _FakeReviewer:
    """Reviewer stub returning a fixed payload for every candidate."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.calls: list[dict] = []

    async def review(self, candidate):
        self.calls.append(candidate)
        return self._payload


def _candidate(symbol: str = "600519.SH", quant_score: float = 82) -> dict:
    return {
        "symbol": symbol,
        "name": "贵州茅台",
        "quant_score": quant_score,
        "factor_scores": {"momentum": 72, "liquidity": 91, "quality": 96, "risk_control": 82},
        "tradability": {"is_tradable": True},
        "risk_flags": [],
        "quant_decision": "BUY",
    }


def test_apply_llm_reviews_available_reuses_reviewer_and_fuses():
    async def run():
        reviewer = _FakeReviewer({
            "llm_view": "positive",
            "catalyst_strength": "likely",
            "llm_confidence": 78,
            "risk_override": False,
            "invalidates_quant": False,
            "key_catalysts": ["催化"],
            "key_risks": ["风险"],
            "risk_flags": [],
            "reasoning": "成立",
        })
        candidates = [_candidate("600519.SH"), _candidate("300750.SZ", 74)]
        warnings, meta = await apply_llm_reviews(
            candidates,
            config={},
            trade_date="2026-06-30",
            style="medium_term",
            reviewer=reviewer,
            review_limit=5,
            enrich=False,
        )
        assert meta == {"enabled": True, "available": True, "reviewed": 2, "review_limit": 2}
        assert warnings == []
        assert len(reviewer.calls) == 2
        c = candidates[0]
        assert c["fusion_mode"] == "quant_llm_fused"
        assert c["llm_confidence"] == 78
        assert c["reasoning"] == "成立"
        assert c["key_catalysts"] == ["催化"]
        assert c["key_risks"] == ["风险"]
        assert c["final_decision"] == "BUY"
        assert c["llm_review"]["reasoning"] == "成立"

    asyncio.run(run())


def test_apply_llm_reviews_review_limit_truncates():
    async def run():
        reviewer = _FakeReviewer({"llm_view": "neutral", "llm_confidence": 50, "reasoning": "中性"})
        candidates = [_candidate(f"60000{i}.SH", 80) for i in range(4)]
        warnings, meta = await apply_llm_reviews(
            candidates,
            config={},
            trade_date="2026-06-30",
            style="medium_term",
            reviewer=reviewer,
            review_limit=2,
            enrich=False,
        )
        assert meta["reviewed"] == 2
        assert meta["review_limit"] == 2
        assert len(reviewer.calls) == 2
        assert candidates[0]["llm_review"]  # reviewed
        assert candidates[2]["llm_review_status"] == "skipped_by_review_limit"
        assert "llm_review" not in candidates[2]

    asyncio.run(run())


def test_apply_llm_reviews_unavailable_degrades_visibly():
    async def run():
        candidates = [_candidate("600519.SH"), _candidate("300750.SZ", 74)]
        warnings, meta = await apply_llm_reviews(
            candidates,
            config={},  # no quick/deep think llm → build_candidate_reviewer returns None
            trade_date="2026-06-30",
            style="medium_term",
            reviewer=None,
            unavailable_warning="custom unavailable msg",
        )
        assert meta == {"enabled": True, "available": False, "reviewed": 0}
        assert warnings == ["custom unavailable msg"]
        # quant BUY → WATCHLIST; stage tagged so the frontend can explain it
        assert candidates[0]["decision_stage"] == "llm_unavailable"
        assert candidates[0]["final_decision"] == "WATCHLIST"
        assert candidates[0]["signal"] == "WATCHLIST"
        assert candidates[0]["position_pct"] == 0.0

    asyncio.run(run())


def test_apply_llm_reviews_disabled_returns_disabled_meta():
    async def run():
        candidates = [_candidate()]
        warnings, meta = await apply_llm_reviews(
            candidates,
            config={},
            trade_date="2026-06-30",
            style="medium_term",
            enabled=False,
            disabled_warning="disabled msg",
        )
        assert meta == {"enabled": False, "reviewed": 0}
        assert warnings == ["disabled msg"]
        assert "decision_stage" not in candidates[0]

    asyncio.run(run())


def test_apply_llm_reviews_empty_candidates_is_noop():
    async def run():
        warnings, meta = await apply_llm_reviews(
            [],
            config={},
            trade_date="2026-06-30",
            style="medium_term",
        )
        assert meta == {"enabled": True, "reviewed": 0}
        assert warnings == []

    asyncio.run(run())


def test_apply_llm_reviews_review_failure_keeps_quant_only():
    async def run():
        class BrokenReviewer:
            async def review(self, candidate):
                raise RuntimeError("boom")

        candidates = [_candidate("600519.SH")]
        warnings, meta = await apply_llm_reviews(
            candidates,
            config={},
            trade_date="2026-06-30",
            style="medium_term",
            reviewer=BrokenReviewer(),
            review_limit=5,
            enrich=False,
        )
        assert meta["reviewed"] == 0
        assert any("LLM review failed for 600519.SH" in w for w in warnings)
        assert candidates[0].get("llm_review") is None

    asyncio.run(run())


def test_coerce_llm_review_dict_and_passthrough():
    review = coerce_llm_review({"llm_view": "positive", "llm_confidence": 80, "reasoning": "x"})
    assert review.llm_view == "positive"
    assert review.llm_confidence == 80
    assert coerce_llm_review(review) is review


def test_candidate_lesson_hits_scope_matching():
    lessons = [
        {"scope": "global", "finding": "g", "id": 1},
        {"scope": "symbol", "target": "600519.SH", "finding": "s", "id": 2},
        {"scope": "symbol", "target": "000001.SZ", "finding": "other", "id": 3},
        {"scope": "industry", "target": "消费 白酒", "finding": "i", "id": 4},
    ]
    cand = {"symbol": "600519.SH", "industry": "消费 白酒", "data_coverage": {}}
    hits = candidate_lesson_hits(cand, lessons)
    ids = [h["id"] for h in hits]
    assert 1 in ids and 2 in ids and 4 in ids and 3 not in ids
    assert candidate_lesson_hits(cand, []) == []


def test_candidate_lesson_hits_industry_normalized():
    # Industry-scope lessons are keyed by the coarse taxonomy group (e.g. the
    # miner stores target="地产"), but candidates carry a raw industry like
    # "房地产". The match must normalize the candidate industry, or the lesson
    # would never fire.
    lessons = [
        {"scope": "industry", "target": "地产", "finding": "neutral", "id": 10},
    ]
    for raw in ("房地产", "房地产开发", "建筑"):
        cand = {"symbol": "000002.SZ", "industry": raw, "data_coverage": {}}
        ids = [h["id"] for h in candidate_lesson_hits(cand, lessons)]
        assert 10 in ids, f"expected 地产 lesson to match raw industry {raw!r}"
    # A different coarse group must NOT match.
    other = {"symbol": "600519.SH", "industry": "白酒", "data_coverage": {}}
    assert candidate_lesson_hits(other, lessons) == []
