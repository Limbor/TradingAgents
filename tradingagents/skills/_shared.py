"""Shared utilities for skill implementations.

Extracted from daily_pipeline and market_scanner to eliminate code duplication.
These functions are pure helpers — they do not interact with external services.
"""

from __future__ import annotations

from typing import Any

from tradingagents.core.signal_fusion import fuse_candidate_signal, quant_evidence_markdown
from tradingagents.dataflows.mcp_adapter import normalize_quant_candidate

# Common constant shared by daily_pipeline and market_scanner
FACTOR_DATA_SOURCE = "stockmanager_mcp"

# ---------------------------------------------------------------------------
# Demo / sample data
# ---------------------------------------------------------------------------

_DEMO_ROWS: list[dict[str, Any]] = [
    {
        "ts_code": "600519.SH", "name": "贵州茅台", "industry": "消费 白酒",
        "quant_score": 82.0, "factor_scores": {"momentum": 72, "liquidity": 92, "quality": 96, "risk_control": 85},
        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
        "risk_flags": [], "score_explain": ["demo quality strong", "demo liquidity strong"],
    },
    {
        "ts_code": "300750.SZ", "name": "宁德时代", "industry": "新能源 电池",
        "quant_score": 76.5, "factor_scores": {"momentum": 78, "liquidity": 91, "quality": 82, "risk_control": 71},
        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
        "risk_flags": [], "score_explain": ["demo momentum strong"],
    },
    {
        "ts_code": "601899.SH", "name": "紫金矿业", "industry": "有色 黄金 铜",
        "quant_score": 71.0, "factor_scores": {"momentum": 74, "liquidity": 88, "quality": 73, "risk_control": 68},
        "tradability": {"is_tradable": True, "st_flag": False, "limit_status": "normal"},
        "risk_flags": [], "score_explain": ["demo trend above average"],
    },
]


def demo_candidates(
    limit: int = 3,
    style: str = "medium_term",
    *,
    include_board: bool = False,
) -> list[dict[str, Any]]:
    """Generate demo candidates for when MCP is unavailable.

    Args:
        limit: Max number of candidates to return.
        style: Investment style for signal fusion.
        include_board: If True, add 'board' field via board_for_symbol().
    """
    candidates = []
    for row in _DEMO_ROWS[:limit]:
        candidate = normalize_quant_candidate(row)
        if include_board:
            candidate["board"] = board_for_symbol(candidate.get("symbol") or candidate.get("ts_code"))
        candidate.update(fuse_candidate_signal(candidate, style))
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = candidate_rationale(candidate)
        candidates.append(candidate)
    return candidates


# ---------------------------------------------------------------------------
# Candidate helpers
# ---------------------------------------------------------------------------


def candidate_rationale(candidate: dict[str, Any], *, include_llm: bool = False) -> str:
    """Build a human-readable rationale string for a candidate.

    Args:
        candidate: Candidate dict with score_explain, factor_scores, etc.
        include_llm: When True, include LLM review reasoning if present.
    """
    if include_llm:
        llm_review = candidate.get("llm_review") if isinstance(candidate.get("llm_review"), dict) else {}
        llm_reasoning = llm_review.get("reasoning") if llm_review else candidate.get("reasoning")
    else:
        llm_reasoning = None

    explains = candidate.get("score_explain") or []
    parts: list[str] = []
    if explains:
        parts.append("; ".join(str(item) for item in explains))
    if llm_reasoning:
        parts.append(f"LLM: {llm_reasoning}")
    if parts:
        return " | ".join(parts)
    fs = candidate.get("factor_scores") if isinstance(candidate.get("factor_scores"), dict) else {}
    if fs:
        return ", ".join(f"{key} {value}" for key, value in fs.items())
    return "StockManager quant ranking candidate."


def factor_profile_for_style(style: str) -> str:
    """Map investment style to MCP factor profile name."""
    if style == "short_term":
        return "short_term_momentum"
    if style == "long_term":
        return "long_term_quality"
    return "medium_term_balanced"


def default_filters(
    *,
    board_filter: str = "all",
    exclude_boards: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the default MCP filter dict.

    Args:
        board_filter: One of 'all', 'main_board', 'dual_growth_only'.
        exclude_boards: Optional list of boards to exclude.
        config: App config dict; if present, merges daily_pipeline_filters.
    """
    filters: dict[str, Any] = {
        "exclude_st": True,
        "exclude_suspended": True,
        "exclude_one_price_limit": True,
        "min_amount_20d": 0,
    }
    if board_filter != "all":
        filters["board_filter"] = mcp_board_filter(board_filter)
    if exclude_boards:
        filters["exclude_boards"] = exclude_boards
    extra = (config or {}).get("daily_pipeline_filters")
    if isinstance(extra, dict):
        filters.update(extra)
    return filters


# ---------------------------------------------------------------------------
# Board classification helpers
# ---------------------------------------------------------------------------


def board_for_symbol(value: Any) -> str:
    """Classify a symbol into its board category."""
    symbol = str(value or "").upper()
    code = symbol.split(".")[0]
    if code.startswith(("300", "301", "302")):
        return "chinext"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("83", "87", "43", "920")):
        return "beijing"
    return "main"


def mcp_board_filter(value: str) -> str:
    """Convert user-facing board filter value to MCP API format."""
    if value == "dual_growth_only":
        return "chinext_star"
    return value
