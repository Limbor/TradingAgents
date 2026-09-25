"""Regression tests for the historical signal-fusion replay."""

import asyncio

from scripts.backtest_signal_fusion import (
    calculate_net_forward_return,
    compute_comparison_metrics,
    fuse_for_backtest,
)
from tradingagents.core.llm_candidate_review import CandidateLLMReview


def _candidate():
    return {
        "quant_score": 80.0,
        "quant_decision": "BUY",
        "tradability": {"is_tradable": True},
        "risk_flags": [],
    }


def test_quant_only_replay_does_not_call_reviewer():
    class Reviewer:
        async def review(self, _candidate):
            raise AssertionError("quant-only replay must not invoke the LLM")

    result = asyncio.run(
        fuse_for_backtest(
            _candidate(), style="medium_term", mode="quant_only", reviewer=Reviewer()
        )
    )
    assert result["fusion_mode"] == "quant_only"


def test_fused_replay_uses_real_review_payload():
    class Reviewer:
        async def review(self, _candidate):
            return CandidateLLMReview(
                llm_view="positive",
                llm_score=72,
                llm_confidence=91,
                catalyst_strength="likely",
                catalyst_score=70,
                risk_assessment="low",
            )

    result = asyncio.run(
        fuse_for_backtest(
            _candidate(), style="medium_term", mode="fused", reviewer=Reviewer()
        )
    )
    assert result["fusion_mode"] == "quant_llm_fused"
    assert result["llm_score"] == 72
    assert result["llm_confidence"] == 91
    assert result["llm_score_source"] == "explicit"


def test_fused_replay_fails_closed_without_reviewer():
    try:
        asyncio.run(fuse_for_backtest(_candidate(), style="medium_term", mode="fused"))
    except RuntimeError as exc:
        assert "reviewer is unavailable" in str(exc)
    else:
        raise AssertionError("fused replay must not silently degrade to quant-only")


def test_forward_return_uses_next_open_and_round_trip_costs():
    rows = [
        {"trade_date": "20240102", "open": 9, "close": 10},
        {"trade_date": "20240103", "open": 10, "close": 10.2},
        {"trade_date": "20240104", "open": 10.2, "close": 11},
    ]
    value = calculate_net_forward_return(
        rows,
        signal_date="2024-01-02",
        horizon=2,
        transaction_cost_bps=10,
        slippage_bps=5,
    )
    expected = (11 * (1 - 0.0015) / (10 * (1 + 0.0015)) - 1) * 100
    assert value == round(expected, 4)


def test_comparison_metrics_are_paired_by_date_symbol():
    base = {"trade_date": "2024-01-02", "symbol": "600000.SH", "horizon": 5,
            "forward_return_pct": 2.0, "signal": "WATCHLIST"}
    rows = [
        {**base, "variant": "quant_only", "direction_correct": False},
        {**base, "variant": "fused", "signal": "BUY", "direction_correct": True},
    ]
    metrics = compute_comparison_metrics(rows)
    assert metrics["paired_count"] == 1
    assert metrics["fused_improved"] == 1
    assert metrics["paired_accuracy_delta_pp"] == 100.0
