from scripts.compare_selection_analysis import build_report


def test_compare_report_flags_reversal_and_legacy_missing_explanation():
    report = build_report(
        [
            {
                "artifact_id": "a1",
                "subject_id": "600519.SH",
                "payload": {
                    "ticker": "600519.SH",
                    "analysis_date": "2026-07-04",
                    "selection_context": {
                        "final_decision": "BUY",
                        "trade_date": "2026-07-04",
                    },
                    "structured_conclusion": {
                        "rating": "Underweight",
                        "plan": {"plan_action": "REDUCE"},
                    },
                },
            },
            {
                "artifact_id": "a2",
                "subject_id": "000001.SZ",
                "payload": {
                    "selection_context": {"final_decision": "WATCHLIST"},
                    "structured_conclusion": {
                        "rating": "Hold",
                        "plan": {"plan_action": "HOLD"},
                    },
                },
            },
            {"artifact_id": "no-selection", "payload": {"structured_conclusion": {}}},
        ]
    )

    assert report["total_stock_reports"] == 3
    assert report["paired_reports"] == 2
    assert report["status_counts"] == {"aligned": 1, "reversal": 1}
    assert report["requires_review"] == 1
    assert report["unexplained_review"] == 1
    assert report["plan_conflicts"] == 0
