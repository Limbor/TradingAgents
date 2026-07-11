"""Shared utilities for skill implementations.

Extracted from daily_pipeline and market_scanner to eliminate code duplication.
These functions are pure helpers — they do not interact with external services.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from tradingagents.core.signal_fusion import fuse_candidate_signal, quant_evidence_markdown
from tradingagents.core.trading_time import get_temporal_context
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
    as_of = (
        candidate.get("price_trade_date")
        or candidate.get("as_of_date")
        or candidate.get("trade_date")
        or ""
    )
    as_of_suffix = f"（基准日 {as_of}）" if as_of else ""
    if parts:
        return " | ".join(parts) + as_of_suffix
    fs = candidate.get("factor_scores") if isinstance(candidate.get("factor_scores"), dict) else {}
    if fs:
        return ", ".join(f"{key} {value}" for key, value in fs.items()) + as_of_suffix
    return f"StockManager 量化排名候选{as_of_suffix}"


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
            Empty/None strings are normalized to 'all' so the FilterPanel
            default (``""``) does not produce an invalid MCP filter.
        exclude_boards: Optional list of boards to exclude.
        config: App config dict; if present, merges daily_pipeline_filters.
    """
    filters: dict[str, Any] = {
        "exclude_st": True,
        "exclude_suspended": True,
        "exclude_one_price_limit": True,
        "min_amount_20d": 0,
    }
    bf = (board_filter or "").strip() or "all"
    if bf != "all":
        filters["board_filter"] = mcp_board_filter(bf)
    if exclude_boards:
        filters["exclude_boards"] = exclude_boards
    extra = (config or {}).get("daily_pipeline_filters")
    if isinstance(extra, dict):
        # Normalize any board_filter coming from the persisted FilterPanel too.
        if "board_filter" in extra:
            extra_bf = (str(extra.get("board_filter") or "").strip()) or "all"
            extra = {**extra, "board_filter": extra_bf}
        filters.update(extra)
    return filters


# Acceptable board_filter values (used by resolve_board_filter).
BOARD_FILTER_VALUES = {"all", "main_board", "dual_growth_only"}


def resolve_board_filter(
    input_filter: str | None,
    config: dict[str, Any] | None,
) -> str:
    """Resolve the effective board_filter with a single priority chain.

    Priority (highest first):
    1. ``input_filter`` — an explicit value from the user message / API call
       (anything other than ``"all"`` / empty wins).
    2. ``config["daily_pipeline_filters"]["board_filter"]`` — the value saved
       by the Dashboard FilterPanel (the global "workbench" setting).
    3. ``config["daily_pipeline_board_filter"]`` — the top-level env override
       (``TRADINGAGENTS_DAILY_PIPELINE_BOARD_FILTER``), kept as a fallback.
    4. ``"all"``.

    This makes the Dashboard FilterPanel the single source of truth for all
    selection-style skills (daily_pipeline, daily_review, chat-triggered
    screening), while still letting a user override it inline in chat.
    """
    candidate = (str(input_filter or "").strip()) or "all"
    if candidate in BOARD_FILTER_VALUES and candidate != "all":
        return candidate
    cfg = config or {}
    fp = cfg.get("daily_pipeline_filters")
    if isinstance(fp, dict):
        fp_val = (str(fp.get("board_filter") or "").strip()) or "all"
        if fp_val in BOARD_FILTER_VALUES and fp_val != "all":
            return fp_val
    env_val = (str(cfg.get("daily_pipeline_board_filter") or "").strip()) or "all"
    if env_val in BOARD_FILTER_VALUES and env_val != "all":
        return env_val
    return "all"


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


# ---------------------------------------------------------------------------
# Temporal context resolution
# ---------------------------------------------------------------------------


def resolve_temporal_context(
    config: dict[str, Any],
    raw_date: str,
    *,
    market: str,
    date_field: str,
    params: BaseModel | None = None,
) -> tuple[Any, BaseModel | None]:
    """Resolve the trading-day temporal context shared by selection skills.

    Centralizes the pattern duplicated by ``daily_pipeline``,
    ``stock_analysis`` and ``daily_review``: compute the "current" temporal
    context, decide whether the caller's ``raw_date`` is the default (today or
    the context ``now`` day), and if not re-resolve against the requested date.

    When ``params`` is given, the ``date_field`` on it is synced to
    ``temporal_context.market_asof_date`` via ``model_copy`` (only when it
    differs), mirroring what each skill previously did inline. When ``params``
    is ``None`` only the temporal context is returned (the caller owns the
    ``model_copy``); this is used by ``daily_pipeline`` whose
    ``_apply_runtime_defaults`` couples the date sync with board-filter
    resolution in a single ``model_copy``.

    Returns ``(temporal_context, updated_params)``. ``updated_params`` is the
    (possibly copied) params when ``params`` is given, otherwise ``None``.
    """
    current = get_temporal_context(config, market=market)
    is_current_default = raw_date in {date.today().isoformat(), current.now[:10]}
    temporal_context = (
        current
        if is_current_default
        else get_temporal_context(config, market=market, requested_date=raw_date)
    )
    updated_params = params
    if params is not None:
        current_value = getattr(params, date_field, None)
        if current_value != temporal_context.market_asof_date:
            updated_params = params.model_copy(
                update={date_field: temporal_context.market_asof_date}
            )
    return temporal_context, updated_params


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def optional_float(value: Any) -> float | None:
    """Coerce ``value`` to ``float`` returning ``None`` for empty/invalid input.

    Handles ``None`` and ``""`` (returns ``None`` without invoking ``float``),
    and swallows ``TypeError``/``ValueError`` for any other non-numeric value.
    """
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
