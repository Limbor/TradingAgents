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


def test_data_coverage_infers_quality_from_ttm_factor_names():
    """Contract emits `roe_ttm` / `gross_margin_ttm`; coverage inference must
    recognize the `_ttm` suffix variants instead of falsely flagging missing."""
    candidate = normalize_quant_candidate(
        {
            "ts_code": "600519.SH",
            "name": "贵州茅台",
            "quant_score": 80,
            "data_coverage": {"quality": "missing"},
            "factor_snapshot": {"roe_ttm": 0.31, "gross_margin_ttm": 0.91},
        }
    )

    # Returned quality factor should upgrade the group to available.
    assert candidate["data_coverage"]["quality"] == "available"


def test_data_coverage_keeps_quality_missing_when_no_quality_factor():
    """When the source truly returns no quality factor, the missing flag must
    survive (no default backfill)."""
    candidate = normalize_quant_candidate(
        {
            "ts_code": "000967.SZ",
            "name": "盈峰环境",
            "quant_score": 70,
            "data_coverage": {"quality": "missing"},
            "key_metrics": {
                "momentum_20d": 0.1166,
                "volatility_20d": 0.0601,
                "pb": 2.548,
                "net_mf_ratio": -0.1081,
                "amount_20d": 1208717436,
            },
        }
    )

    assert candidate["data_coverage"]["quality"] == "missing"
    # Other categories present in the metrics should be inferred available.
    assert candidate["data_coverage"]["momentum"] == "available"
    assert candidate["data_coverage"]["valuation"] == "available"
