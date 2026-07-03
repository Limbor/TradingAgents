from tradingagents.dataflows.mcp_adapter import (
    evidence_from_mcp_payload,
    mcp_payload_to_markdown,
    normalize_quant_candidate,
)


def test_evidence_card_from_mcp_payload():
    card = evidence_from_mcp_payload(
        "600519.SH",
        {
            "as_of_date": "2026-06-26",
            "source": "tushare",
            "adj_type": "qfq",
            "industry": "食品饮料",
            "tradability": {"is_st": False},
            "risk_flags": ["问询函"],
            "factor_snapshot": {"roe_pct": 0.9},
            "warnings": ["sample"],
        },
    )

    assert card.as_dict()["ts_code"] == "600519.SH"
    markdown = card.to_markdown()
    assert "食品饮料" in markdown
    assert "roe_pct=0.9" in markdown
    assert "问询函" in markdown


def test_mcp_payload_to_markdown_rows():
    markdown = mcp_payload_to_markdown(
        "Daily",
        {
            "as_of_date": "2026-06-26",
            "source": "tushare",
            "rows": [{"trade_date": "2026-06-26", "close": 100}],
        },
    )

    assert "| trade_date | close |" in markdown
    assert "as_of_date: 2026-06-26" in markdown


def test_normalize_quant_candidate_preserves_decision_fields():
    candidate = normalize_quant_candidate(
        {
            "rank": 1,
            "ts_code": "600519.SH",
            "name": "贵州茅台",
            "industry": "食品饮料",
            "quant_score": 82.4,
            "decision": "BUY",
            "decision_reason": "quality/liquidity gates passed",
            "gate_reasons": ["quality_gate_passed", "tradability_gate_passed"],
            "key_metrics": {"roe": 18.5, "amount_20d": 12.3},
            "data_coverage": {"valuation": "available", "flow": "missing"},
            "board": "main",
            "factor_scores": {"quality": 96},
        }
    )

    assert candidate["quant_decision"] == "BUY"
    assert candidate["quant_decision_reason"] == "quality/liquidity gates passed"
    assert candidate["quant_gate_reasons"] == ["quality_gate_passed", "tradability_gate_passed"]
    assert candidate["key_metrics"]["roe"] == 18.5
    assert candidate["data_coverage"]["flow"] == "missing"
    assert candidate["board"] == "main"


def test_normalize_quant_candidate_legacy_payload_falls_back_safely():
    candidate = normalize_quant_candidate(
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "score": 63,
            "factor_scores": {"momentum": 55},
        }
    )

    assert candidate["quant_decision"] == "WATCHLIST"
    assert any("data_coverage unavailable" in warning for warning in candidate["warnings"])
