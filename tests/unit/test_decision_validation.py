import pytest

from tradingagents.core.decision_validation import validate_decision_history


def test_exit_is_correct_when_price_falls():
    result = validate_decision_history([{
        "snapshot_payload": {"final_decision": "EXIT"},
        "outcome_payload": {"actual_return": -0.08},
    }], min_samples=1)
    metrics = result["by_decision"]["EXIT"]
    assert metrics["win_rate"] == 1.0
    assert metrics["average_return"] == -0.08
    assert metrics["average_directional_return"] == 0.08
    assert result["effectiveness_claim_allowed"] is True


def _case(decision: str, actual_return):
    return {
        "snapshot_payload": {"final_decision": decision},
        "outcome_payload": {"actual_return": actual_return},
    }


def test_history_validation_groups_decisions_and_gates_small_samples():
    cases = [_case("BUY", 0.1), _case("BUY", -0.05), _case("WATCHLIST", 0.02), _case("BUY", None)]
    result = validate_decision_history(cases, min_samples=3)

    assert result["overall"]["sample_count"] == 2
    assert result["overall"]["win_rate"] == pytest.approx(1 / 2, abs=1e-4)
    assert result["by_decision"]["BUY"]["average_return"] == 0.025
    assert result["by_decision"]["BUY"]["statistically_usable"] is False
    assert result["strategy_claims_allowed"] is False
    assert result["effectiveness_claim_allowed"] is False
    assert result["pending_ratio"] == 0.5
    assert result["warnings"]
