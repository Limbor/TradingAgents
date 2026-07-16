"""Market scanner skill backed by StockManager quant ranking."""

from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.candidate_review_runner import apply_llm_reviews
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.signal_fusion import fuse_candidate_signal, quant_evidence_markdown
from tradingagents.core.trading_time import get_temporal_context
from tradingagents.dataflows.mcp_adapter import (
    normalize_quant_candidate,
    payload_rows,
    payload_warnings,
)
from tradingagents.skills._shared import (
    FACTOR_DATA_SOURCE,
    candidate_rationale,
    default_filters,
    demo_candidates,
    factor_profile_for_style,
)
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress


class MarketScannerInput(BaseModel):
    """Input for market scanning."""

    market: Literal["cn_a", "us"] = Field(default="cn_a")
    theme: str | None = Field(default=None, description="Optional sector/theme filter")
    min_score: int = Field(default=65, ge=0, le=100)
    limit: int = Field(default=5, ge=1, le=20)


class MarketScannerOutput(BaseModel):
    """Scanner output."""

    market: str
    candidates: list[dict[str, Any]]
    mcp_used: bool
    warnings: list[str] = Field(default_factory=list)


class MarketScannerSkill(BaseSkill):
    """Screen equities and emit ranked candidates with AI-ready rationale."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="market_scanner",
            name="Market Scanner",
            description=(
                "Screen A-share or US market candidates by momentum, liquidity, "
                "quality, and risk-adjusted setup score."
            ),
            version="1.0.0",
            triggers=["scanner", "scan", "筛选", "选股", "找股票", "市场扫描", "机会"],
            icon="radar",
            category="scanner",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MarketScannerInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return MarketScannerOutput

    async def execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        input_params: MarketScannerInput = params
        yield SkillEvent(
            event_type="skill_start",
            data={"skill_id": self.metadata.id, "market": input_params.market},
        )
        yield skill_progress(
            stage_id="prepare",
            stage_label="准备扫描",
            status="completed",
            detail=f"{input_params.market} · top {input_params.limit} · min score {input_params.min_score}",
            progress_pct=5,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Market Scanner",
                "status": f"正在扫描 {'A股' if input_params.market == 'cn_a' else '美股'} 候选池",
            },
        )
        yield skill_progress(
            stage_id="quant_rank",
            stage_label="量化候选池排名",
            status="running",
            detail="正在读取市场候选池并计算因子排名",
            agent="Market Scanner",
            progress_pct=25,
        )

        temporal_context = get_temporal_context(config, market="cn_a") if input_params.market == "cn_a" else None
        market_asof = temporal_context.market_asof_date if temporal_context else ""
        candidates, mcp_used, warnings, quant_meta = await _rank_candidates(input_params, config, market_asof)
        yield skill_progress(
            stage_id="quant_rank",
            stage_label="量化候选池排名",
            status="completed",
            detail=f"获取 {len(candidates)} 个原始候选",
            agent="Market Scanner",
            progress_pct=55,
        )
        candidates = _apply_theme_and_score_filters(candidates, input_params)

        # LLM review: only over real cn_a MCP data. Skip demo/US sample so we
        # never fabricate reasoning over non-real candidates. Degrades visibly
        # (decision_stage="llm_unavailable" + warning) when the reviewer is down.
        review_meta: dict[str, Any] = {"enabled": False, "reviewed": 0}
        if mcp_used and candidates and input_params.market == "cn_a":
            yield skill_progress(
                stage_id="llm_review",
                stage_label="LLM 候选复核",
                status="running",
                detail="正在检查催化剂、风险与近期价格走势原因",
                agent="LLM Reviewer",
                progress_pct=78,
            )
            trade_date = str(config.get("market_scanner_trade_date") or market_asof)
            style = str(config.get("investment_style") or "medium_term")
            review_warnings, review_meta = await _apply_llm_reviews(
                input_params, config, candidates, trade_date=trade_date, style=style
            )
            warnings = [*warnings, *review_warnings]
            yield skill_progress(
                stage_id="llm_review",
                stage_label="LLM 候选复核",
                status="completed",
                detail=f"复核状态: {'可用' if review_meta.get('available') else '降级'}",
                agent="LLM Reviewer",
                progress_pct=85,
            )

        # Transparency: surface which trading day the prices are anchored to and why.
        as_of_date, data_window_note = _data_window(input_params.market, temporal_context, config, quant_meta)
        for candidate in candidates:
            candidate.setdefault("price_trade_date", as_of_date)

        if warnings:
            yield SkillEvent(
                event_type="agent_status",
                data={"agent": "Market Scanner", "status": "；".join(warnings)},
            )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Market Scanner",
                "status": f"正在按最低分 {input_params.min_score} 过滤并排序候选标的",
            },
        )
        yield skill_progress(
            stage_id="filter_sort",
            stage_label="过滤与排序",
            status="completed",
            detail=f"输出 {len(candidates)} 个候选标的",
            agent="Market Scanner",
            progress_pct=90,
        )

        yield SkillEvent(
            event_type="scanner_candidates",
            data={
                "market": input_params.market,
                "candidates": candidates,
                "mcp_used": mcp_used,
                "warnings": warnings,
                "quant_meta": quant_meta,
                "as_of_date": as_of_date,
                "market_asof_date": temporal_context.market_asof_date if temporal_context else None,
                "session_state": temporal_context.session_state if temporal_context else None,
                "calendar_state": temporal_context.calendar_state if temporal_context else None,
                "decision_target_date": temporal_context.decision_target_date if temporal_context else None,
                "data_window_note": data_window_note,
                "review_meta": review_meta,
            },
        )
        report = _render_report(input_params.market, candidates, mcp_used, warnings)
        _save_market_scanner_artifacts(
            config,
            input_params=input_params,
            candidates=candidates,
            report=report,
            mcp_used=mcp_used,
            warnings=warnings,
            quant_meta=quant_meta,
            as_of_date=as_of_date,
            data_window_note=data_window_note,
            review_meta=review_meta,
            temporal_context=temporal_context,
        )

        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "scanner_report",
                "content": report,
                "is_final": True,
            },
        )
        yield skill_progress(
            stage_id="report",
            stage_label="生成扫描报告",
            status="completed",
            detail="候选表和可读报告已生成",
            progress_pct=100,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "market": input_params.market,
                "candidates": candidates,
                "mcp_used": mcp_used,
                "warnings": warnings,
                "quant_meta": quant_meta,
                "as_of_date": as_of_date,
                "data_window_note": data_window_note,
                "review_meta": review_meta,
            },
        )

    async def cancel(self) -> None:
        return None


async def _rank_candidates(
    input_params: MarketScannerInput,
    config: dict[str, Any],
    market_asof_date: str,
) -> tuple[list[dict[str, Any]], bool, list[str], dict[str, Any]]:
    if input_params.market == "us":
        candidates = [_score_us_sample(item) for item in _US_SAMPLE_UNIVERSE]
        return candidates, False, ["US scanner currently uses a local sample universe."], {"source": "us_sample"}

    client = await get_mcp_client(config)
    if client is None:
        if config.get("market_scanner_demo_fallback", False):
            return _demo_candidates(), False, ["StockManager MCP 不可用，当前为显式 demo fallback。"], {"source": "demo_fallback"}
        return [], False, ["StockManager MCP 不可用，已停止真实 A 股筛选。"], {"source": "unavailable"}

    payload = await client.rank_factor_candidates(
        universe_index=str(config.get("market_scanner_universe_index") or "000906.SH"),
        trade_date=str(config.get("market_scanner_trade_date") or market_asof_date),
        style=str(config.get("investment_style") or "medium_term"),
        limit=max(input_params.limit, 20),
        candidate_limit=int(config.get("market_scanner_candidate_limit", 120)),
        factor_profile=factor_profile_for_style(str(config.get("investment_style") or "medium_term")),
        filters=default_filters(),
        return_factor_snapshot=True,
    )
    warnings = payload_warnings(payload)
    rows = payload_rows(payload)
    status = (payload or {}).get("status")
    if status == "error" or not rows:
        message = (payload or {}).get("message") or "StockManager returned no ranked candidates."
        return [], True, [*warnings, str(message)], _quant_meta(payload)

    candidates = []
    for row in rows:
        candidate = normalize_quant_candidate(row)
        candidate.update(fuse_candidate_signal(candidate, str(config.get("investment_style") or "medium_term")))
        candidate["setup"] = _setup_from_candidate(candidate)
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = candidate_rationale(candidate)
        candidates.append(candidate)
    return candidates, True, warnings, _quant_meta(payload)


def _get_score(item: dict[str, Any]) -> float:
    return float(item.get("score") or item.get("quant_score") or 0)


def _apply_theme_and_score_filters(
    candidates: list[dict[str, Any]],
    input_params: MarketScannerInput,
) -> list[dict[str, Any]]:
    filtered = [
        item
        for item in candidates
        if not input_params.theme
        or input_params.theme.lower() in str(item.get("theme", "")).lower()
        or input_params.theme in str(item.get("name", ""))
        or input_params.theme in str(item.get("industry", ""))
    ]
    scored = sorted(filtered, key=_get_score, reverse=True)
    return [item for item in scored if _get_score(item) >= input_params.min_score][: input_params.limit]


def _demo_candidates() -> list[dict[str, Any]]:
    """Demo fallback using shared demo data."""
    candidates = demo_candidates(limit=3, style="medium_term")
    for c in candidates:
        c["setup"] = _setup_from_candidate(c)
    return candidates


def _save_market_scanner_artifacts(
    config: dict[str, Any],
    *,
    input_params: MarketScannerInput,
    candidates: list[dict[str, Any]],
    report: str,
    mcp_used: bool,
    warnings: list[str],
    quant_meta: dict[str, Any],
    as_of_date: str | None,
    data_window_note: str,
    review_meta: dict[str, Any],
    temporal_context: Any,
) -> None:
    counts = _decision_counts(candidates)
    title = f"市场扫描 {input_params.market.upper()}"
    subtitle = f"Top {len(candidates)} · min score {input_params.min_score:g}"
    summary = (
        f"输出 {len(candidates)} 个候选，BUY {counts.get('BUY', 0)} 个，"
        f"WATCHLIST {counts.get('WATCHLIST', 0)} 个"
    )
    payload = {
        "market": input_params.market,
        "limit": input_params.limit,
        "min_score": input_params.min_score,
        "theme": input_params.theme,
        "candidates": candidates,
        "mcp_used": mcp_used,
        "warnings": warnings,
        "quant_meta": quant_meta,
        "as_of_date": as_of_date,
        "market_asof_date": temporal_context.market_asof_date if temporal_context else None,
        "session_state": temporal_context.session_state if temporal_context else None,
        "calendar_state": temporal_context.calendar_state if temporal_context else None,
        "decision_target_date": temporal_context.decision_target_date if temporal_context else None,
        "data_window_note": data_window_note,
        "review_meta": review_meta,
    }
    tags = ["market_scanner", input_params.market]
    save_skill_artifact(
        config,
        skill_id="market_scanner",
        artifact_type="scanner_report",
        title=title,
        subtitle=subtitle,
        subject_type="market",
        subject_id=input_params.market,
        subject_name=input_params.market.upper(),
        summary=summary,
        content_markdown=report,
        payload=payload,
        tags=tags,
    )
    save_skill_artifact(
        config,
        skill_id="market_scanner",
        artifact_type="signal_pack",
        title=f"{title} 信号包",
        subtitle=subtitle,
        subject_type="market",
        subject_id=input_params.market,
        subject_name=input_params.market.upper(),
        summary=summary,
        payload=payload,
        tags=[*tags, "signal_pack"],
    )


def _decision_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in candidates:
        decision = str(item.get("final_decision") or item.get("signal") or item.get("quant_decision") or "UNKNOWN").upper()
        counts[decision] = counts.get(decision, 0) + 1
    return counts


_US_SAMPLE_UNIVERSE: list[dict[str, Any]] = [
    {"symbol": "NVDA", "name": "NVIDIA", "theme": "AI Semiconductors", "momentum": 86, "liquidity": 98, "quality": 91, "risk": 55},
    {"symbol": "MSFT", "name": "Microsoft", "theme": "Cloud AI", "momentum": 70, "liquidity": 96, "quality": 93, "risk": 24},
    {"symbol": "AAPL", "name": "Apple", "theme": "Consumer Hardware", "momentum": 54, "liquidity": 95, "quality": 88, "risk": 22},
    {"symbol": "META", "name": "Meta Platforms", "theme": "AI Advertising", "momentum": 78, "liquidity": 91, "quality": 84, "risk": 39},
    {"symbol": "TSLA", "name": "Tesla", "theme": "EV Robotics", "momentum": 67, "liquidity": 94, "quality": 62, "risk": 70},
]


def _score_us_sample(item: dict[str, Any]) -> dict[str, Any]:
    score = (
        item["momentum"] * 0.35
        + item["liquidity"] * 0.25
        + item["quality"] * 0.30
        - item["risk"] * 0.10
    )
    candidate = {
        **item,
        "score": round(score, 1),
        "quant_score": round(score, 1),
        "factor_data_source": "us_sample",
        "factor_scores": {
            "momentum": item["momentum"],
            "liquidity": item["liquidity"],
            "quality": item["quality"],
            "risk_control": max(0, 100 - item["risk"]),
        },
        "tradability": {"is_tradable": True},
        "risk_flags": [],
        "score_explain": ["local US sample universe"],
    }
    candidate.update(fuse_candidate_signal(candidate, "medium_term"))
    candidate["setup"] = _setup_from_candidate(candidate)
    candidate["rationale"] = candidate_rationale(candidate)
    return candidate


def _quant_meta(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    return {
        "source": FACTOR_DATA_SOURCE if payload else "unavailable",
        "status": payload.get("status"),
        "method": payload.get("method"),
        "as_of_date": payload.get("as_of_date"),
        "factor_profile": payload.get("factor_profile"),
        "universe": payload.get("universe") or {},
    }


def _setup_from_candidate(candidate: dict[str, Any]) -> str:
    if candidate.get("signal") == "BUY":
        return "quant_buy"
    if candidate.get("risk_flags"):
        return "risk_watch"
    return "watchlist"


def _render_report(
    market: str,
    candidates: list[dict[str, Any]],
    mcp_used: bool,
    warnings: list[str],
) -> str:
    title = "A-share Market Scanner" if market == "cn_a" else "US Market Scanner"
    if not candidates:
        warning_text = "\n".join(f"- {warning}" for warning in warnings)
        return (
            f"## {title}\n\n"
            f"- Factor source: **{FACTOR_DATA_SOURCE if mcp_used else 'unavailable/sample'}**\n\n"
            f"No candidates passed the current score threshold.\n\n{warning_text}"
        )
    lines = [
        f"## {title}",
        "",
        f"- Source: **{'StockManager MCP quant ranking' if mcp_used else 'local sample/demo'}**",
        f"- Factor source: **{FACTOR_DATA_SOURCE if mcp_used else 'sample/demo'}**",
        *([f"- Warning: {warning}" for warning in warnings] if warnings else []),
        "",
        "| Rank | Symbol | Name | Signal | Final | Quant | Setup | Theme |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    for idx, item in enumerate(candidates, start=1):
        lines.append(
            f"| {idx} | {item.get('symbol', '?')} | {item.get('name', '?')} | {item.get('signal', '')} | "
            f"{item.get('final_score', item.get('score', ''))} | {item.get('quant_score', item.get('score', ''))} | "
            f"{item.get('setup', '')} | {item.get('theme', '')} |"
        )
    lines.extend(
        [
            "",
            "### Execution Notes",
            "",
            "- Treat scanner output as a research queue, not an automatic trade list.",
            "- Confirm company-specific news, liquidity, and market regime before execution.",
            "- For A-shares, check limit-up/down status and T+1 constraints before placing orders.",
        ]
    )
    return "\n".join(lines)


async def _apply_llm_reviews(
    input_params: MarketScannerInput,
    config: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    trade_date: str,
    style: str,
) -> tuple[list[str], dict[str, Any]]:
    """Run the shared LLM reviewer over scanner candidates.

    Uses ``market_scanner_llm_review_*`` config keys; degrades visibly when the
    reviewer cannot be built (returns a warning the frontend can surface).
    """
    enabled = bool(config.get("market_scanner_llm_review_enabled", True))
    reviewer = config.get("market_scanner_llm_reviewer")
    review_limit = int(config.get("market_scanner_llm_review_limit", 5) or 0)
    enrich = bool(config.get("market_scanner_llm_review_enrich", True))
    return await apply_llm_reviews(
        candidates,
        config=config,
        trade_date=trade_date,
        style=style,
        reviewer=reviewer,
        review_limit=review_limit,
        enabled=enabled,
        strategy_lessons=None,
        enrich=enrich,
        disabled_warning="Market scanner LLM review disabled by config.",
        unavailable_warning="Market scanner LLM reviewer unavailable; using quant-only fusion.",
    )


def _data_window(
    market: str,
    temporal_context: Any,
    config: dict[str, Any],
    quant_meta: dict[str, Any],
) -> tuple[str | None, str]:
    """Return ``(as_of_date, human-readable note)`` explaining the price anchor.

    The note tells the user *why* the scan may be using the previous trading
    day's close (pre-market), today's close (post-close), or the most recent
    trading day (non-trading day) — addressing the "why did it fetch
    yesterday's data" confusion directly in the UI.
    """
    override = config.get("market_scanner_trade_date")
    price_as_of = quant_meta.get("as_of_date") if isinstance(quant_meta, dict) else None
    if market != "cn_a" or temporal_context is None:
        note = "US 样本宇宙（演示数据，无实时交易日历）" if market == "us" else "无交易日历上下文"
        return (str(price_as_of) if price_as_of else None), note
    if override:
        as_of = str(override)
        return (str(price_as_of) or as_of), f"使用指定交易日 {as_of} 收盘数据"
    as_of = str(price_as_of or temporal_context.market_asof_date)
    session = temporal_context.session_state
    if session == "before_close_data":
        note = f"盘前：当日收盘价尚未产生，使用前一交易日 {temporal_context.market_asof_date} 收盘数据"
    elif session == "after_close_data":
        note = f"盘后：使用当日 {temporal_context.market_asof_date} 收盘数据"
    elif session == "non_trading":
        note = f"非交易日：使用最近交易日 {temporal_context.market_asof_date} 收盘数据"
    else:
        note = f"数据截至 {temporal_context.market_asof_date}"
    return as_of, note


skill = MarketScannerSkill()
