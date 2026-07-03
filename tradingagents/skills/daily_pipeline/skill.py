"""Daily A-share selection pipeline."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

from tradingagents.core.candidate_enrichment import CandidateContext, enrich_candidates
from tradingagents.core.llm_candidate_review import CandidateLLMReview, build_candidate_reviewer
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.persistence import Database
from tradingagents.core.signal_fusion import fuse_candidate_signal, quant_evidence_markdown
from tradingagents.dataflows.mcp_adapter import normalize_quant_candidate, payload_rows, payload_warnings
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress

logger = logging.getLogger(__name__)

FACTOR_DATA_SOURCE = "stockmanager_mcp"
BOARD_FILTER_VALUES = {"all", "main_board", "dual_growth_only"}


class DailyPipelineInput(BaseModel):
    trade_date: str = Field(default_factory=lambda: date.today().isoformat())
    universe_index: str = Field(default="000906.SH", description="Default CSI800")
    limit: int = Field(default=5, ge=1, le=20)
    candidate_limit: int = Field(default=80, ge=5, le=800)
    board_filter: Literal["all", "main_board", "dual_growth_only"] = Field(
        default="all",
        description="A-share board filter: all, main_board (exclude STAR/ChiNext), or dual_growth_only.",
    )
    exclude_boards: list[str] = Field(
        default_factory=list,
        description="Additional board exclusions: star, chinext, beijing, main.",
    )


class DailyPipelineOutput(BaseModel):
    trade_date: str
    candidates: list[dict[str, Any]]
    profile: dict[str, Any]
    mcp_used: bool


class DailyPipelineSkill(BaseSkill):
    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="daily_pipeline",
            name="Daily Pipeline",
            description="Run MCP-backed A-share daily screening and produce a research queue.",
            version="1.0.0",
            triggers=["daily pipeline", "每日选股", "早报", "今日机会", "每日扫描"],
            icon="calendar-check",
            category="scanner",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return DailyPipelineInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return DailyPipelineOutput

    async def execute(self, params: BaseModel, config: dict[str, Any]) -> AsyncIterator[SkillEvent]:
        input_params: DailyPipelineInput = params
        input_params = _apply_runtime_defaults(input_params, config)
        db = config.get("db") or Database()
        profile = db.get_user_profile()

        yield SkillEvent(
            event_type="skill_start",
            data={
                "skill_id": self.metadata.id,
                "trade_date": input_params.trade_date,
                "universe_index": input_params.universe_index,
                "board_filter": input_params.board_filter,
                "exclude_boards": input_params.exclude_boards,
            },
        )
        yield skill_progress(
            stage_id="prepare",
            stage_label="准备每日选股",
            status="completed",
            detail=f"{input_params.trade_date} · {input_params.universe_index} · {input_params.board_filter}",
            progress_pct=5,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={"agent": "Daily Pipeline", "status": "正在加载指数成分股和交易日上下文"},
        )
        yield skill_progress(
            stage_id="universe",
            stage_label="候选池与交易日上下文",
            status="running",
            detail="正在加载指数成分股、板块过滤和交易状态",
            agent="Daily Pipeline",
            progress_pct=15,
        )

        candidates, mcp_used, warnings, quant_meta = await _rank_candidates(input_params, config, profile)
        yield skill_progress(
            stage_id="universe",
            stage_label="候选池与交易日上下文",
            status="completed",
            detail=f"量化层返回 {len(candidates)} 个候选",
            agent="Daily Pipeline",
            progress_pct=35,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Factor Scorer",
                "status": f"正在按 {profile['investment_style']} 风格读取 StockManager 量化排名",
            },
        )
        yield skill_progress(
            stage_id="quant_rank",
            stage_label="量化因子排名",
            status="completed",
            detail=f"策略风格: {profile['investment_style']}",
            agent="Factor Scorer",
            progress_pct=50,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={"agent": "LLM Reviewer", "status": "正在复核量化候选的催化剂、风险和可交易性"},
        )
        yield skill_progress(
            stage_id="llm_review",
            stage_label="LLM 候选复核",
            status="running",
            detail="正在检查催化剂、风险、公告和资金流上下文",
            agent="LLM Reviewer",
            progress_pct=60,
        )
        review_warnings, review_meta = await _apply_llm_reviews(input_params, config, profile, candidates)
        yield skill_progress(
            stage_id="llm_review",
            stage_label="LLM 候选复核",
            status="completed",
            detail=f"复核状态: {'可用' if review_meta.get('available') else '降级'}",
            agent="LLM Reviewer",
            progress_pct=78,
        )
        warnings = [*warnings, *review_warnings]
        strategy_meta = quant_meta.get("strategy_meta") or {}
        quant_candidates = _candidate_cards(candidates, include_llm=False)
        reviewed_candidates = _candidate_cards(candidates, include_llm=True)
        decision_pack = _decision_pack(candidates)

        yield SkillEvent(
            event_type="daily_pipeline_candidates",
            data={
                "trade_date": input_params.trade_date,
                "universe_index": input_params.universe_index,
                "board_filter": input_params.board_filter,
                "exclude_boards": input_params.exclude_boards,
                "mcp_used": mcp_used,
                "warnings": warnings,
                "quant_meta": quant_meta,
                "strategy_meta": strategy_meta,
                "review_meta": review_meta,
                "profile": profile,
                "candidates": candidates,
                "quant_candidates": quant_candidates,
                "reviewed_candidates": reviewed_candidates,
                "decision_pack": decision_pack,
                "action_suggestions": [
                    {"label": "深度分析第1名", "action": "analyze_top1"},
                    {"label": "查看行业分布", "action": "view_sector_distribution"},
                ],
            },
        )
        report = _render_report(input_params, candidates, profile, mcp_used, warnings)
        yield SkillEvent(
            event_type="agent_status",
            data={"agent": "Report Writer", "status": "正在生成每日选股早报"},
        )
        yield skill_progress(
            stage_id="report",
            stage_label="生成每日选股报告",
            status="running",
            detail="正在保存信号、报告和候选决策包",
            agent="Report Writer",
            progress_pct=88,
        )
        signal_result = _save_candidate_signals(db, str(config.get("run_id", "")), input_params.trade_date, candidates)
        try:
            db.save_report(
                report_id=str(uuid.uuid4()),
                run_id=str(config.get("run_id", "")),
                ticker="DAILY_PIPELINE",
                ticker_name="每日选股",
                rating="Watchlist",
                content=report,
                path=None,
            )
        except Exception as exc:
            logger.warning("Failed to save daily pipeline report to DB: %s", exc)

        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "daily_pipeline_report",
                "content": report,
                "is_final": True,
            },
        )
        yield skill_progress(
            stage_id="report",
            stage_label="生成每日选股报告",
            status="completed",
            detail=f"保存 {signal_result.get('total', 0) - signal_result.get('failed', 0)} 条信号",
            agent="Report Writer",
            progress_pct=100,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "trade_date": input_params.trade_date,
                "candidates": candidates,
                "profile": profile,
                "mcp_used": mcp_used,
                "quant_meta": quant_meta,
                "strategy_meta": strategy_meta,
                "review_meta": review_meta,
                "quant_candidates": quant_candidates,
                "reviewed_candidates": reviewed_candidates,
                "decision_pack": decision_pack,
                "signal_persistence": signal_result,
            },
        )

    async def cancel(self) -> None:
        return None


def _apply_runtime_defaults(input_params: DailyPipelineInput, config: dict[str, Any]) -> DailyPipelineInput:
    updates: dict[str, Any] = {}
    configured_filter = str(config.get("daily_pipeline_board_filter") or "").strip()
    if input_params.board_filter == "all" and configured_filter in BOARD_FILTER_VALUES:
        updates["board_filter"] = configured_filter
    if not updates:
        return input_params
    return input_params.model_copy(update=updates)


async def _rank_candidates(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, list[str], dict[str, Any]]:
    client = await get_mcp_client(config)
    if client is None:
        if config.get("daily_pipeline_demo_fallback", False):
            return _demo_candidates(input_params.limit, profile["investment_style"]), False, [
                "StockManager MCP unavailable; using explicit demo fallback."
            ], {"source": "demo_fallback"}
        return [], False, ["StockManager MCP unavailable; quant ranking not available."], {"source": "unavailable"}

    mcp_limit = _mcp_fetch_limit(input_params)
    payload = await client.rank_factor_candidates(
        universe_index=input_params.universe_index,
        trade_date=input_params.trade_date,
        style=profile["investment_style"],
        limit=mcp_limit,
        candidate_limit=input_params.candidate_limit,
        factor_profile=_factor_profile_for_style(profile["investment_style"]),
        filters=_default_filters(input_params, config),
        sector_prefs=profile.get("sector_prefs") or [],
        return_factor_snapshot=True,
        enable_decision=True,
        max_per_industry=config.get("daily_pipeline_max_per_industry", 3),
        decision_config=config.get("daily_pipeline_decision_config"),
    )
    warnings = payload_warnings(payload)
    rows = payload_rows(payload)
    status = (payload or {}).get("status")
    if status == "error" or not rows:
        message = (payload or {}).get("message") or "StockManager returned no ranked candidates."
        if _is_universe_empty(message):
            fallback_payload, fallback_warning = await _rank_candidates_with_previous_universe(
                client, input_params, profile, config
            )
            fallback_rows = payload_rows(fallback_payload)
            if fallback_rows:
                payload = fallback_payload
                rows = fallback_rows
                warnings = [*warnings, str(message), fallback_warning]
            else:
                return [], True, [*warnings, str(message)], _quant_meta(payload, input_params, profile)
        else:
            return [], True, [*warnings, str(message)], _quant_meta(payload, input_params, profile)

    rows, board_warnings = _filter_rows_by_board(rows, input_params)
    warnings.extend(board_warnings)

    candidates = []
    for row in rows[: input_params.limit]:
        candidate = normalize_quant_candidate(row)
        candidate["board"] = _board_for_symbol(candidate.get("symbol") or candidate.get("ts_code"))
        _attach_payload_meta(candidate, payload)
        fusion = fuse_candidate_signal(candidate, profile["investment_style"])
        candidate.update(fusion)
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = _candidate_rationale(candidate)
        candidates.append(candidate)
    return candidates, True, warnings, _quant_meta(payload, input_params, profile)


async def _rank_candidates_with_previous_universe(
    client: Any,
    input_params: DailyPipelineInput,
    profile: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    config = config or {}
    fallback_dates = _fallback_universe_dates(input_params.trade_date)
    for fallback_date in fallback_dates:
        payload = await client.rank_factor_candidates(
            universe_index=input_params.universe_index,
            trade_date=fallback_date,
            style=profile["investment_style"],
            limit=_mcp_fetch_limit(input_params),
            candidate_limit=input_params.candidate_limit,
            factor_profile=_factor_profile_for_style(profile["investment_style"]),
            filters=_default_filters(input_params, config),
            sector_prefs=profile.get("sector_prefs") or [],
            return_factor_snapshot=True,
            enable_decision=True,
            max_per_industry=config.get("daily_pipeline_max_per_industry", 3),
            decision_config=config.get("daily_pipeline_decision_config"),
        )
        if payload_rows(payload):
            return payload, (
                f"StockManager universe empty for {input_params.trade_date}; "
                f"used previous available ranking date {fallback_date}. "
                f"Tushare index_weight is MONTHLY (month-end data)."
            )
    return None, f"StockManager universe empty for {input_params.trade_date}; all fallback dates also returned no rows."


def _fallback_universe_dates(trade_date: str) -> list[str]:
    """Generate fallback dates: nearby calendar days + month-end dates.

    Tushare index_weight data is published monthly at month-end, so we need
    to search beyond a few calendar days to find available constituent data.
    """
    try:
        current = datetime.strptime(trade_date, "%Y-%m-%d").date()
    except ValueError:
        return []

    # Strategy 1: nearby calendar days (up to 10 days)
    nearby = [(current - timedelta(days=offset)).isoformat() for offset in range(1, 11)]

    # Strategy 2: month-end dates going back 6 months
    month_ends = []
    d = current
    for _ in range(6):
        d = d.replace(day=1) - timedelta(days=1)  # last day of previous month
        month_ends.append(d.isoformat())

    # Deduplicate while preserving order
    seen: set[str] = set()
    result = []
    for dt_str in nearby + month_ends:
        if dt_str not in seen and dt_str < trade_date:
            seen.add(dt_str)
            result.append(dt_str)
    return result


def _previous_calendar_dates(value: str, *, days: int) -> list[str]:  # pragma: no cover – legacy, unused
    """Legacy: return previous calendar dates. Prefer _fallback_universe_dates."""
    try:
        current = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return []
    return [(current - timedelta(days=offset)).isoformat() for offset in range(1, days + 1)]


def _is_universe_empty(message: Any) -> bool:
    text = str(message)
    return "UNIVERSE_EMPTY" in text or "成分股为空" in text


def _mcp_fetch_limit(input_params: DailyPipelineInput) -> int:
    """Fetch a wider pool so local board filters can still fill the final limit."""
    if input_params.board_filter == "all" and not input_params.exclude_boards:
        return input_params.limit
    return min(input_params.candidate_limit, max(input_params.limit * 8, input_params.limit + 20))


def _filter_rows_by_board(
    rows: list[dict[str, Any]],
    input_params: DailyPipelineInput,
) -> tuple[list[dict[str, Any]], list[str]]:
    allowed = _allowed_boards(input_params)
    excluded = {item.lower().strip().replace("-", "_") for item in input_params.exclude_boards if item}
    if allowed is None and not excluded:
        return rows, []

    filtered: list[dict[str, Any]] = []
    removed: dict[str, int] = {}
    for row in rows:
        board = _board_for_symbol(row.get("ts_code") or row.get("symbol"))
        if (allowed is not None and board not in allowed) or board in excluded:
            removed[board] = removed.get(board, 0) + 1
            continue
        row = dict(row)
        row["board"] = board
        filtered.append(row)

    warnings = []
    if removed:
        removed_text = ", ".join(f"{board}:{count}" for board, count in sorted(removed.items()))
        warnings.append(
            f"Board filter applied ({input_params.board_filter}); removed {removed_text}. "
            f"Returned {len(filtered)} of {len(rows)} fetched candidates."
        )
    if len(filtered) < input_params.limit:
        warnings.append(
            f"Board filter left only {len(filtered)} candidates for requested limit {input_params.limit}; "
            "increase candidate_limit or relax board_filter."
        )
    return filtered, warnings


def _allowed_boards(input_params: DailyPipelineInput) -> set[str] | None:
    if input_params.board_filter == "main_board":
        return {"main"}
    if input_params.board_filter == "dual_growth_only":
        return {"chinext", "star"}
    return None


def _board_for_symbol(value: Any) -> str:
    symbol = str(value or "").upper()
    code = symbol.split(".")[0]
    if code.startswith(("300", "301", "302")):
        return "chinext"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("83", "87", "43", "920")):
        return "beijing"
    return "main"


def _demo_candidates(limit: int, style: str) -> list[dict[str, Any]]:
    rows = [
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
    candidates = []
    for row in rows[:limit]:
        candidate = normalize_quant_candidate(row)
        candidate["board"] = _board_for_symbol(candidate.get("symbol") or candidate.get("ts_code"))
        candidate.update(fuse_candidate_signal(candidate, style))
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = _candidate_rationale(candidate)
        candidates.append(candidate)
    return candidates


def _save_candidate_signals(db: Database, run_id: str, trade_date: str, candidates: list[dict[str, Any]]) -> dict[str, int]:
    failed = 0
    for candidate in candidates:
        try:
            db.save_signal(
                signal_id=str(uuid.uuid4()),
                run_id=run_id,
                trade_date=trade_date,
                symbol=str(candidate.get("symbol") or candidate.get("ts_code") or ""),
                name=str(candidate.get("name") or ""),
                signal=str(candidate.get("signal") or "WATCHLIST"),
                final_score=_optional_float(candidate.get("final_score")),
                quant_score=_optional_float(candidate.get("quant_score")),
                llm_confidence=_optional_float(candidate.get("llm_confidence")),
                fusion_mode=str(candidate.get("fusion_mode") or ""),
                payload=candidate,
            )
        except Exception as exc:
            logger.warning("Failed to save daily pipeline signal for %s: %s", candidate.get("symbol"), exc)
            failed += 1
    if failed:
        logger.warning("Daily pipeline signal persistence: %d/%d failed", failed, len(candidates))
    return {"total": len(candidates), "failed": failed}


async def _apply_llm_reviews(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
    profile: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    if not candidates:
        return [], {"enabled": bool(config.get("daily_pipeline_llm_review_enabled", True)), "reviewed": 0}

    enabled = bool(config.get("daily_pipeline_llm_review_enabled", True))
    if not enabled:
        return ["Daily pipeline LLM review disabled by config."], {"enabled": False, "reviewed": 0}

    reviewer = config.get("daily_pipeline_llm_reviewer")
    if reviewer is None:
        reviewer = build_candidate_reviewer(
            config,
            style=str(profile.get("investment_style") or "medium_term"),
            trade_date=input_params.trade_date,
        )
    if reviewer is None:
        for candidate in candidates:
            candidate["decision_stage"] = "llm_unavailable"
            candidate["final_decision"] = "WATCHLIST" if candidate.get("quant_decision") == "BUY" else candidate.get("final_decision", candidate.get("signal", "MONITOR"))
            candidate["signal"] = candidate["final_decision"]
            if candidate["final_decision"] != "BUY":
                candidate["position_pct"] = 0.0
        return ["Daily pipeline LLM reviewer unavailable; using quant-only fusion."], {
            "enabled": True,
            "available": False,
            "reviewed": 0,
        }

    review_limit = int(config.get("daily_pipeline_llm_review_limit", min(5, len(candidates))) or 0)
    review_limit = max(0, min(review_limit, len(candidates)))
    warnings: list[str] = []
    reviewed = 0

    # Enrich candidates with real-time data (news, announcements, northbound flow)
    context_map: dict[str, CandidateContext] = {}
    try:
        context_map = await enrich_candidates(
            candidates[:review_limit],
            input_params.trade_date,
            config,
        )
    except Exception as exc:
        logger.warning("Candidate enrichment failed; proceeding without context: %s", exc)

    # Concurrent LLM reviews via asyncio.gather
    async def _do_review(candidate: dict[str, Any]) -> tuple[dict[str, Any], Any | None, Exception | None]:
        try:
            symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "")
            ctx = context_map.get(symbol)
            # Pass context if reviewer supports it; gracefully degrade otherwise
            if "context" in inspect.signature(reviewer.review).parameters:
                raw_review = await reviewer.review(candidate, context=ctx)
            else:
                raw_review = await reviewer.review(candidate)
            return candidate, raw_review, None
        except Exception as exc:
            return candidate, None, exc

    review_tasks = [_do_review(c) for c in candidates[:review_limit]]
    results = await asyncio.gather(*review_tasks)

    for candidate, raw_review, exc in results:
        if exc is not None:
            symbol = candidate.get("symbol") or candidate.get("ts_code") or "unknown"
            logger.warning("LLM review failed for %s: %s", symbol, exc)
            warnings.append(f"LLM review failed for {symbol}; kept quant-only signal.")
            continue
        review = _coerce_llm_review(raw_review)
        candidate["llm_review"] = review.model_dump()
        candidate["key_catalysts"] = review.key_catalysts
        candidate["key_risks"] = review.key_risks
        candidate.update(
            fuse_candidate_signal(
                candidate,
                str(profile.get("investment_style") or "medium_term"),
                review.as_fusion_payload(),
            )
        )
        candidate["rationale"] = _candidate_rationale(candidate)
        reviewed += 1

    for candidate in candidates[review_limit:]:
        candidate["llm_review_status"] = "skipped_by_review_limit"

    return warnings, {
        "enabled": True,
        "available": True,
        "reviewed": reviewed,
        "review_limit": review_limit,
    }


def _coerce_llm_review(value: Any) -> CandidateLLMReview:
    if isinstance(value, CandidateLLMReview):
        return value
    if isinstance(value, dict):
        return CandidateLLMReview.model_validate(value)
    if isinstance(value, BaseModel):
        return CandidateLLMReview.model_validate(value.model_dump())
    raise TypeError(f"Unsupported LLM review payload: {type(value)!r}")


def _attach_payload_meta(candidate: dict[str, Any], payload: dict[str, Any] | None) -> None:
    payload = payload or {}
    candidate["strategy_meta"] = payload.get("strategy_meta") or {}
    candidate["strategy_meta_available"] = bool(payload.get("strategy_meta"))
    candidate["selection_meta"] = payload.get("selection_meta") or {}
    candidate["concentration_meta"] = payload.get("concentration_meta") or {}


def _candidate_cards(candidates: list[dict[str, Any]], *, include_llm: bool) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for item in candidates:
        card = {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "industry": item.get("industry"),
            "board": item.get("board"),
            "rank": item.get("rank"),
            "quant_score": item.get("quant_score"),
            "universe_percentile": item.get("universe_percentile"),
            "quant_decision": item.get("quant_decision"),
            "quant_decision_reason": item.get("quant_decision_reason"),
            "factor_scores": item.get("factor_scores"),
            "key_metrics": item.get("key_metrics"),
            "data_coverage": item.get("data_coverage"),
            "quant_gate_reasons": item.get("quant_gate_reasons"),
            "warnings": item.get("warnings"),
        }
        if include_llm:
            card.update(
                {
                    "llm_view": item.get("llm_view"),
                    "catalyst_strength": item.get("catalyst_strength"),
                    "risk_assessment": item.get("risk_assessment"),
                    "llm_review": item.get("llm_review"),
                    "key_catalysts": item.get("key_catalysts"),
                    "key_risks": item.get("key_risks"),
                    "final_decision": item.get("final_decision") or item.get("signal"),
                    "decision_stage": item.get("decision_stage"),
                    "display_score": item.get("display_score", item.get("final_score")),
                    "gate_reasons": item.get("gate_reasons") or [],
                    "action_plan": item.get("action_plan") or {},
                    "position_pct": item.get("position_pct", 0.0),
                }
            )
        cards.append(card)
    return cards


def _decision_pack(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "quant_decision": item.get("quant_decision"),
            "final_decision": item.get("final_decision") or item.get("signal"),
            "decision_stage": item.get("decision_stage"),
            "display_score": item.get("display_score", item.get("final_score")),
            "gate_reasons": item.get("gate_reasons") or [],
            "action_plan": item.get("action_plan") or {},
            "position_pct": item.get("position_pct", 0.0),
        }
        for item in candidates
    ]


def _quant_meta(payload: dict[str, Any] | None, input_params: DailyPipelineInput, profile: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return {
        "source": FACTOR_DATA_SOURCE if payload else "unavailable",
        "status": payload.get("status"),
        "method": payload.get("method"),
        "as_of_date": payload.get("as_of_date") or input_params.trade_date,
        "factor_profile": payload.get("factor_profile") or _factor_profile_for_style(profile["investment_style"]),
        "universe": payload.get("universe") or {},
        "strategy_meta": payload.get("strategy_meta") or {},
        "strategy_meta_available": bool(payload.get("strategy_meta")),
        "selection_meta": payload.get("selection_meta") or {},
        "concentration_meta": payload.get("concentration_meta") or {},
    }


def _candidate_rationale(candidate: dict[str, Any]) -> str:
    llm_review = candidate.get("llm_review") if isinstance(candidate.get("llm_review"), dict) else {}
    llm_reasoning = llm_review.get("reasoning") if llm_review else candidate.get("reasoning")
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


def _factor_profile_for_style(style: str) -> str:
    if style == "short_term":
        return "short_term_momentum"
    if style == "long_term":
        return "long_term_quality"
    return "medium_term_balanced"


def _default_filters(input_params: DailyPipelineInput | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "exclude_st": True,
        "exclude_suspended": True,
        "exclude_one_price_limit": True,
        "min_amount_20d": 0,
    }
    if input_params is not None:
        if input_params.board_filter != "all":
            filters["board_filter"] = _mcp_board_filter(input_params.board_filter)
        if input_params.exclude_boards:
            filters["exclude_boards"] = input_params.exclude_boards
    extra = (config or {}).get("daily_pipeline_filters")
    if isinstance(extra, dict):
        filters.update(extra)
    return filters


def _mcp_board_filter(value: str) -> str:
    if value == "dual_growth_only":
        return "chinext_star"
    return value


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_report(
    input_params: DailyPipelineInput,
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    mcp_used: bool,
    warnings: list[str],
) -> str:
    lines = [
        "## Daily A-share Research Queue",
        "",
        f"- Trade date: **{input_params.trade_date}**",
        f"- Universe: **{input_params.universe_index}**",
        f"- Board filter: **{input_params.board_filter}**",
        f"- Investment style: **{profile['investment_style']}**",
        f"- Source: **{'StockManager MCP quant ranking' if mcp_used else 'unavailable/demo'}**",
        f"- Factor source: **{FACTOR_DATA_SOURCE if mcp_used else 'demo/unavailable'}**",
    ]
    if warnings:
        lines.append(f"- Warnings: {'; '.join(warnings)}")
    lines.extend(
        [
            "",
            "| Rank | Symbol | Name | Board | Quant | Final | Stage | Display | Position | Gate reasons | Rationale |",
            "|---:|---|---|---|---|---|---|---:|---:|---|---|",
        ]
    )
    for idx, item in enumerate(candidates, 1):
        gates = ", ".join(str(reason) for reason in item.get("gate_reasons") or [])
        lines.append(
            f"| {idx} | {item.get('symbol', '?')} | {item.get('name', '?')} | {item.get('board', '')} | "
            f"{item.get('quant_decision', '')} | "
            f"{item.get('final_decision', item.get('signal', ''))} | "
            f"{item.get('decision_stage', '')} | "
            f"{item.get('display_score', item.get('final_score', item.get('score', '')))} | "
            f"{item.get('position_pct', 0)}% | "
            f"{gates} | {item.get('rationale', '')} |"
        )
    return "\n".join(lines)


skill = DailyPipelineSkill()
