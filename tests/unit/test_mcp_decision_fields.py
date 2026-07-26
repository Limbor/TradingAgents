"""Tests for MCP contract §4.6 decision/confidence field consumption.

Covers the Agent-side integration of the structured MCP fields:
- rows[].decision as {action, demoted, demote_reasons, reason} (dict) vs legacy string
- score_confidence / confidence_breakdown normalization and fusion down-weighting
- mcp_demoted audit trail in decision_gate
- quant_evidence_markdown injection for the LLM review prompt
- daily_pipeline demoted-row deprioritization before industry capping
"""

from tradingagents.core.signal_fusion import (
    decision_gate,
    fuse_candidate_signal,
    quant_evidence_markdown,
)
from tradingagents.dataflows.mcp_adapter import normalize_quant_candidate
from tradingagents.skills.daily_pipeline.skill import _deprioritize_demoted_rows

DEMOTE_REASON = "risk_control: risk_control=25 < 阈值 50"


def _fused_assessment(**overrides):
    assessment = {
        "llm_confidence": 75.0,
        "catalyst_score": None,
        "llm_view": "positive",
        "catalyst_strength": "likely",
        "risk_override": False,
        "invalidates_quant": False,
        "risk_flags": [],
    }
    assessment.update(overrides)
    return assessment


class TestNormalizeDictDecision:
    def test_dict_decision_parsed_into_quant_fields(self):
        candidate = normalize_quant_candidate(
            {
                "ts_code": "688072.SH",
                "quant_score": 78.4,
                "decision": {
                    "action": "MONITOR",
                    "demoted": True,
                    "demote_reasons": [DEMOTE_REASON],
                    "reason": "risk hard gate triggered",
                },
            }
        )
        assert candidate["quant_decision"] == "MONITOR"
        assert candidate["quant_demoted"] is True
        assert candidate["quant_demote_reasons"] == [DEMOTE_REASON]
        assert candidate["quant_decision_reason"] == "risk hard gate triggered"
        # No str(dict) garbage may leak into the decision field.
        assert "{" not in candidate["quant_decision"]

    def test_string_decision_backward_compat(self):
        candidate = normalize_quant_candidate(
            {
                "ts_code": "600519.SH",
                "quant_score": 82.0,
                "decision": "BUY",
                "decision_reason": "quality gates passed",
            }
        )
        assert candidate["quant_decision"] == "BUY"
        assert candidate["quant_decision_reason"] == "quality gates passed"
        assert candidate["quant_demoted"] is False
        assert candidate["quant_demote_reasons"] == []

    def test_score_confidence_clamped_with_breakdown(self):
        candidate = normalize_quant_candidate(
            {
                "ts_code": "600036.SH",
                "quant_score": 70.0,
                "score_confidence": 1.7,
                "confidence_breakdown": ["quality partial: -0.12"],
            }
        )
        assert candidate["score_confidence"] == 1.0
        assert candidate["confidence_breakdown"] == ["quality partial: -0.12"]

        low = normalize_quant_candidate(
            {"ts_code": "600036.SH", "quant_score": 70.0, "score_confidence": -0.2}
        )
        assert low["score_confidence"] == 0.0

    def test_confidence_fields_absent_when_not_provided(self):
        candidate = normalize_quant_candidate({"ts_code": "000001.SZ", "quant_score": 60.0})
        assert "score_confidence" not in candidate
        assert "confidence_breakdown" not in candidate


class TestScoreConfidenceFusion:
    def test_confidence_shifts_weight_to_llm_in_fused_mode(self):
        candidate = {
            "quant_score": 70.0,
            "score_confidence": 0.88,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "medium_term", _fused_assessment())
        # medium_term alpha 0.55 * confidence 0.88 = 0.484
        assert result["alpha_weight"]["quant"] == 0.48
        assert result["alpha_weight"]["llm"] == 0.52

    def test_full_confidence_leaves_weight_unchanged(self):
        candidate = {
            "quant_score": 70.0,
            "score_confidence": 1.0,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "medium_term", _fused_assessment())
        assert result["alpha_weight"]["quant"] == 0.55

    def test_quant_only_mode_ignores_confidence(self):
        candidate = {
            "quant_score": 70.0,
            "score_confidence": 0.5,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "medium_term")
        assert result["fusion_mode"] == "quant_only"
        assert result["alpha_weight"]["quant"] == 1.0
        assert result["final_score"] == 70.0


class TestDemotedGateAudit:
    def test_mcp_demoted_appended_to_gate_reasons(self):
        candidate = {
            "quant_score": 78.4,
            "quant_decision": "MONITOR",
            "quant_demoted": True,
            "quant_demote_reasons": [DEMOTE_REASON],
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "medium_term")
        # Demoted action stays MONITOR in quant_only mode.
        assert result["final_decision"] == "MONITOR"
        assert any(reason.startswith("mcp_demoted: ") for reason in result["gate_reasons"])
        assert DEMOTE_REASON in " ".join(result["gate_reasons"])

    def test_demoted_monitor_not_upgraded_without_confirmed_catalyst(self):
        candidate = {
            "quant_score": 78.4,
            "quant_decision": "MONITOR",
            "quant_demoted": True,
            "quant_demote_reasons": [DEMOTE_REASON],
            "tradability": {"is_tradable": True},
            "risk_flags": [],
            "factor_snapshot": {"close": 50.0},
        }
        result = fuse_candidate_signal(candidate, "medium_term", _fused_assessment())
        assert result["final_decision"] == "MONITOR"
        assert "default_gate" in result["gate_reasons"]
        assert any(reason.startswith("mcp_demoted: ") for reason in result["gate_reasons"])

    def test_demoted_without_reasons_marks_unspecified(self):
        candidate = {
            "quant_score": 60.0,
            "quant_decision": "MONITOR",
            "quant_demoted": True,
            "tradability": {"is_tradable": True},
            "risk_flags": [],
        }
        pack = decision_gate(candidate, "medium_term")
        assert "mcp_demoted: unspecified" in pack["gate_reasons"]

    def test_raw_dict_decision_normalized_in_gate(self):
        candidate = {
            "quant_score": 78.4,
            "decision": {"action": "MONITOR", "demoted": True},
            "tradability": {"is_tradable": True},
            "risk_flags": [],
        }
        pack = decision_gate(candidate, "medium_term")
        # dict decision must resolve to its action, never str(dict).
        assert pack["final_decision"] == "MONITOR"


class TestEvidenceMarkdown:
    def test_demoted_and_confidence_lines_rendered(self):
        candidate = {
            "symbol": "688072.SH",
            "name": "拓荆科技",
            "quant_score": 78.4,
            "quant_decision": "MONITOR",
            "quant_demoted": True,
            "quant_demote_reasons": [DEMOTE_REASON],
            "score_confidence": 0.88,
            "confidence_breakdown": ["quality partial: -0.12"],
        }
        markdown = quant_evidence_markdown(candidate)
        assert f"- quant_demoted: true ({DEMOTE_REASON})" in markdown
        assert "- score_confidence: 0.88 (quality partial: -0.12)" in markdown

    def test_clean_candidate_omits_new_lines(self):
        markdown = quant_evidence_markdown(
            {"symbol": "600519.SH", "quant_score": 82.0, "quant_decision": "BUY"}
        )
        assert "quant_demoted" not in markdown
        assert "score_confidence" not in markdown


class TestDeprioritizeDemotedRows:
    def test_demoted_rows_moved_behind_clean_rows(self):
        rows = [
            {"ts_code": "688072.SH", "rank": 1, "decision": {"action": "MONITOR", "demoted": True}},
            {"ts_code": "600519.SH", "rank": 2, "decision": {"action": "BUY", "demoted": False}},
            {"ts_code": "002265.SZ", "rank": 3, "decision": {"action": "MONITOR", "demoted": True}},
            {"ts_code": "600036.SH", "rank": 4, "decision": {"action": "BUY", "demoted": False}},
        ]
        reordered, warnings = _deprioritize_demoted_rows(rows)
        assert [row["ts_code"] for row in reordered] == [
            "600519.SH",
            "600036.SH",
            "688072.SH",
            "002265.SZ",
        ]
        assert len(warnings) == 1
        assert "demoted 2 candidate(s)" in warnings[0]
        assert "688072.SH" in warnings[0]

    def test_no_demoted_rows_is_noop(self):
        rows = [
            {"ts_code": "600519.SH", "decision": {"action": "BUY", "demoted": False}},
            {"ts_code": "600036.SH", "decision": "BUY"},
        ]
        reordered, warnings = _deprioritize_demoted_rows(rows)
        assert reordered is rows
        assert warnings == []

    def test_legacy_string_decision_never_demoted(self):
        rows = [{"ts_code": "600519.SH", "decision": "BUY"}]
        reordered, warnings = _deprioritize_demoted_rows(rows)
        assert reordered == rows
        assert warnings == []
