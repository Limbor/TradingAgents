"""Daily A-share selection pipeline."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.core.activity_labels import report_activity
from tradingagents.core.adaptive_alpha import resolve_alpha_override
from tradingagents.core.agent_runtime import runtime_model
from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.candidate_review_runner import apply_llm_reviews
from tradingagents.core.decision_reconciliation import reconcile_selection_analysis
from tradingagents.core.industry_taxonomy import (
    INDUSTRY_SELECTION_ALIASES,
    SW_L1_INDUSTRIES,
    expand_industry_selection_terms,
    resolve_sw_l1_industries,
)
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.model_policy import provider_kwargs, resolve_model
from tradingagents.core.persistence import Database
from tradingagents.core.portfolio_prices import latest_close
from tradingagents.core.reflection_enroll import enroll_reflection_case
from tradingagents.core.signal_fusion import fuse_candidate_signal, quant_evidence_markdown
from tradingagents.core.trading_time import get_temporal_context
from tradingagents.dataflows.mcp_adapter import (
    normalize_quant_candidate,
    payload_error_message,
    payload_rows,
    payload_warnings,
)
from tradingagents.llm_clients import create_llm_client
from tradingagents.skills._shared import (
    FACTOR_DATA_SOURCE,
    board_for_symbol,
    candidate_rationale,
    default_filters,
    demo_candidates,
    drive_with_progress,
    factor_profile_for_style,
    optional_float,
    rank_progress_detail,
    resolve_board_filter,
    resolve_temporal_context,
)
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress

logger = logging.getLogger(__name__)


class DailyPipelineInput(BaseModel):
    trade_date: str = Field(default_factory=lambda: date.today().isoformat())
    universe_index: str = Field(default="000906.SH", description="Default CSI800")
    shadow_universe_indices: list[str] = Field(
        default_factory=list,
        description="Discovery-only index pools. Their candidates never backfill the core Top 5.",
    )
    limit: int = Field(default=5, ge=1, le=20)
    candidate_limit: int = Field(
        default=120,
        ge=5,
        le=800,
        description=(
            "Expensive factor-compute budget. StockManager v2 batch-prefilters the complete "
            "eligible universe before applying this limit."
        ),
    )
    board_filter: Literal["all", "main_board", "dual_growth_only"] = Field(
        default="all",
        description="A-share board filter: all, main_board (exclude STAR/ChiNext), or dual_growth_only.",
    )
    exclude_boards: list[str] = Field(
        default_factory=list,
        description="Additional board exclusions: star, chinext, beijing, main.",
    )
    industries: list[str] = Field(
        default_factory=list,
        description=(
            "行业/板块限定关键词，如 ['半导体', '煤炭']。非空时只保留行业字段匹配的候选，"
            "并作为 sector_prefs 传给量化层加分。"
        ),
    )
    concepts: list[str] = Field(
        default_factory=list,
        description=(
            "需要精确限定成分股的概念板块名称，如 ['CXO概念']。成功解析时通过 "
            "MCP include_symbols 在 candidate_limit 截断前筛选；失败时回退 industries。"
        ),
    )
    industry_taxonomy: Literal["CITICS", "SW2021"] | None = Field(
        default=None,
        description="标准行业分类协议；与 industry_codes 配套使用。",
    )
    industry_level: Literal["L1", "L2", "L3"] | None = Field(
        default=None,
        description="标准行业层级；与 industry_codes 配套使用。",
    )
    industry_codes: list[str] = Field(
        default_factory=list,
        description=(
            "标准行业代码白名单。优先于中文名称过滤，由 Market 页面直接传给 "
            "StockManager，避免同名、别名和分类体系错配。"
        ),
    )


class DailyPipelineOutput(BaseModel):
    trade_date: str
    candidates: list[dict[str, Any]]
    shadow_candidates: list[dict[str, Any]] = Field(default_factory=list)
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
        raw_trade_date = input_params.trade_date
        temporal_context, _ = resolve_temporal_context(
            config, raw_trade_date, market="cn_a", date_field="trade_date"
        )
        input_params = _apply_runtime_defaults(input_params, config, temporal_context)
        db = config.get("db") or Database()
        profile = db.get_user_profile()
        strategy_lessons = _load_strategy_lessons(db, temporal_context.market_asof_date)

        # Adaptive alpha: when enabled, translate the measured prediction
        # scorecard into a production fusion-weight override. Gated by the
        # existing advisory applicability check; disabled => static STYLE_ALPHA.
        alpha_override: float | None = None
        alpha_meta: dict[str, Any] = {"enabled": False}
        if config.get("adaptive_alpha_enabled"):
            alpha_source = str(config.get("adaptive_alpha_source") or "evaluation")
            if alpha_source == "evaluation":
                scorecard = db.get_evaluation_scorecard()
            elif alpha_source == "prediction":
                # Explicit legacy escape hatch for controlled comparisons only.
                scorecard = db.get_prediction_scorecard()
            else:
                raise ValueError(f"unsupported adaptive_alpha_source: {alpha_source}")
            alpha_override, alpha_meta = resolve_alpha_override(
                scorecard, str(profile.get("investment_style") or "medium_term")
            )
            alpha_meta = {"enabled": True, "source": alpha_source, **alpha_meta}

        yield SkillEvent(
            event_type="skill_start",
            data={
                "skill_id": self.metadata.id,
                "trade_date": input_params.trade_date,
                "market_asof_date": temporal_context.market_asof_date,
                "decision_target_date": temporal_context.decision_target_date,
                "info_cutoff": temporal_context.info_cutoff,
                "temporal_context": temporal_context.to_dict(),
                "universe_index": input_params.universe_index,
                "shadow_universe_indices": _shadow_universe_indices(input_params, config),
                "board_filter": input_params.board_filter,
                "exclude_boards": input_params.exclude_boards,
                "industries": input_params.industries,
                "concepts": input_params.concepts,
                "industry_taxonomy": input_params.industry_taxonomy,
                "industry_level": input_params.industry_level,
                "industry_codes": input_params.industry_codes,
            },
        )
        yield skill_progress(
            stage_id="prepare",
            stage_label="准备每日选股",
            status="completed",
            detail=(
                f"{input_params.trade_date} · {input_params.universe_index} · {input_params.board_filter}"
                + (f" · 行业限定: {'/'.join(input_params.industries)}" if input_params.industries else "")
                + (
                    f" · {input_params.industry_taxonomy}/{input_params.industry_level or 'L1'}"
                    if input_params.industry_codes and input_params.industry_taxonomy else ""
                )
                + (f" · 概念限定: {'/'.join(input_params.concepts)}" if input_params.concepts else "")
            ),
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

        # Stream MCP ranking-job progress (async_mode) into the timeline while
        # the quant ranking runs; falls back transparently to the sync call.
        candidates: list[dict[str, Any]] = []
        mcp_used = False
        warnings: list[str] = []
        quant_meta: dict[str, Any] = {}
        async for kind, value in drive_with_progress(
            lambda on_progress: _rank_candidates(
                input_params,
                config,
                profile,
                alpha_override=alpha_override,
                progress_callback=on_progress,
            )
        ):
            if kind == "progress":
                pct = value.get("progress_pct")
                yield skill_progress(
                    stage_id="universe",
                    stage_label="候选池与交易日上下文",
                    status="running",
                    detail=rank_progress_detail(value),
                    agent="Factor Scorer",
                    progress_pct=(
                        15 + float(pct) * 0.2 if isinstance(pct, (int, float)) else None
                    ),
                )
            else:
                candidates, mcp_used, warnings, quant_meta = value
        if not candidates and alpha_meta.get("applied"):
            alpha_meta = {**alpha_meta, "applied": False, "reason": "no_candidates"}
        if alpha_meta.get("applied"):
            warnings = [
                *warnings,
                "自适应α已生效：α "
                f"{alpha_meta.get('static_alpha')}→{alpha_meta.get('suggested_alpha')}"
                f"（有效样本 n={alpha_meta.get('n')}）",
            ]
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
            stage_label="逐只复核候选股票",
            status="running",
            detail="正在检查催化剂、风险、公告和资金流上下文",
            agent="LLM Reviewer",
            progress_pct=60,
        )
        async for kind, value in drive_with_progress(
            lambda on_progress: _apply_llm_reviews(
                input_params,
                {**config, "_activity_progress": on_progress},
                profile,
                candidates,
                strategy_lessons=strategy_lessons,
                alpha_override=alpha_override,
            )
        ):
            if kind == "progress":
                yield SkillEvent(event_type="skill_progress", data=value)
            else:
                review_warnings, review_meta = value
        memory_traces = [row.get("memory_trace") or {} for row in candidates]
        memory_count = sum(len(trace.get("injected_ids") or []) for trace in memory_traces)
        yield skill_progress(
            stage_id="strategy_memory", stage_label="核对历史经验", status="completed",
            detail=(f"候选复核共提供 {memory_count} 次经验参考，可在候选详情查看理由"
                    if memory_count else "本轮候选未注入适用经验，依据当前证据复核"),
        )
        yield skill_progress(
            stage_id="llm_review",
            stage_label="逐只复核候选股票",
            status="completed",
            detail=f"复核状态: {'可用' if review_meta.get('available') else '降级'}",
            agent="LLM Reviewer",
            progress_pct=78,
        )
        warnings = [*warnings, *review_warnings]
        async for kind, value in drive_with_progress(
            lambda on_progress: _apply_deep_analysis(
                input_params, {**config, "_activity_progress": on_progress}, profile, candidates,
            )
        ):
            if kind == "progress":
                yield SkillEvent(event_type="skill_progress", data=value)
            else:
                deep_warnings, deep_meta = value
        warnings = [*warnings, *deep_warnings]
        if deep_meta.get("enabled"):
            yield skill_progress(
                stage_id="deep_analysis",
                stage_label="深入分析优先候选",
                status="completed",
                detail=(
                    f"深度分析 {deep_meta.get('analyzed', 0)} 只，"
                    f"失败 {deep_meta.get('failed', 0)} 只"
                ),
                agent="Deep Analyst",
                progress_pct=84,
            )
        strategy_meta = quant_meta.get("strategy_meta") or {}
        shadow_candidates = quant_meta.get("shadow_candidates") or []
        quant_candidates = _candidate_cards(candidates, include_llm=False)
        reviewed_candidates = _candidate_cards(candidates, include_llm=True)
        for candidate in candidates:
            candidate["market_asof_date"] = temporal_context.market_asof_date
            candidate["decision_target_date"] = temporal_context.decision_target_date
            candidate["decision_id"] = f"signal:{_signal_id(input_params.trade_date, candidate)}"
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
                "deep_meta": deep_meta,
                "adaptive_alpha": alpha_meta,
                "profile": profile,
                "candidates": candidates,
                "shadow_candidates": shadow_candidates,
                "quant_candidates": quant_candidates,
                "reviewed_candidates": reviewed_candidates,
                "decision_pack": decision_pack,
                "action_suggestions": [
                    {"label": "深度分析第1名", "action": "analyze_top1"},
                    {"label": "查看行业分布", "action": "view_sector_distribution"},
                ],
            },
        )
        report = _render_report(input_params, candidates, profile, mcp_used, warnings, temporal_context)
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
        briefing = await _render_llm_briefing(candidates, warnings, temporal_context, config)
        if briefing:
            report = briefing + "\n" + report

        signal_result = _save_candidate_signals(db, str(config.get("run_id", "")), input_params.trade_date, candidates)
        _save_daily_pipeline_artifacts(
            config,
            input_params=input_params,
            report=report,
            candidates=candidates,
            quant_candidates=quant_candidates,
            reviewed_candidates=reviewed_candidates,
            decision_pack=decision_pack,
            warnings=warnings,
            quant_meta=quant_meta,
            signal_result=signal_result,
            temporal_context=temporal_context.to_dict(),
        )
        case_result = _save_reflection_cases(
            db,
            str(config.get("run_id", "")),
            input_params.trade_date,
            candidates,
            str(profile.get("investment_style") or ""),
        )

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
        # Persist decisions to memory log for future reflection
        _store_decisions_to_memory(candidates, input_params.trade_date, config)
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "trade_date": input_params.trade_date,
                "market_asof_date": temporal_context.market_asof_date,
                "decision_target_date": temporal_context.decision_target_date,
                "info_cutoff": temporal_context.info_cutoff,
                "temporal_context": temporal_context.to_dict(),
                "candidates": candidates,
                "shadow_candidates": shadow_candidates,
                "profile": profile,
                "mcp_used": mcp_used,
                "quant_meta": quant_meta,
                "strategy_meta": strategy_meta,
                "review_meta": review_meta,
                "deep_meta": deep_meta,
                "adaptive_alpha": alpha_meta,
                "quant_candidates": quant_candidates,
                "reviewed_candidates": reviewed_candidates,
                "decision_pack": decision_pack,
                "signal_persistence": signal_result,
                "reflection_cases": case_result,
            },
        )

    async def cancel(self) -> None:
        return None


def _apply_runtime_defaults(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
    temporal_context=None,
) -> DailyPipelineInput:
    updates: dict[str, Any] = {}
    temporal_context = temporal_context or get_temporal_context(config, market="cn_a")
    if input_params.trade_date != temporal_context.market_asof_date:
        updates["trade_date"] = temporal_context.market_asof_date
    # Single source of truth for board_filter: explicit input > FilterPanel
    # (daily_pipeline_filters.board_filter) > env (daily_pipeline_board_filter)
    # > "all". See _shared.resolve_board_filter for the full priority chain.
    resolved_filter = resolve_board_filter(input_params.board_filter, config)
    if resolved_filter != input_params.board_filter:
        updates["board_filter"] = resolved_filter
    if not updates:
        return input_params
    return input_params.model_copy(update=updates)


async def _rank_candidates(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
    profile: dict[str, Any],
    alpha_override: float | None = None,
    progress_callback: Any = None,
) -> tuple[list[dict[str, Any]], bool, list[str], dict[str, Any]]:
    client = await get_mcp_client(config)
    if client is None:
        if config.get("daily_pipeline_demo_fallback", False):
            return demo_candidates(input_params.limit, profile["investment_style"], include_board=True), False, [
                "StockManager MCP unavailable; using explicit demo fallback."
            ], {"source": "demo_fallback"}
        raise RuntimeError("StockManager MCP 不可用，无法执行真实量化排名")

    mcp_limit = _mcp_fetch_limit(input_params)
    concept_symbols, concept_warnings = await asyncio.to_thread(
        _resolve_concept_symbol_filter,
        input_params,
    )
    if concept_symbols and not _client_supports_tool_feature(
        client,
        "rank_factor_candidates",
        "symbol_whitelist_prefilter",
    ):
        concept_warnings.append(
            "StockManager MCP does not declare symbol_whitelist_prefilter; "
            "fell back to the mapped SW L1 industry scope."
        )
        concept_symbols = []
    if input_params.concepts and not concept_symbols and not _mcp_include_industries(input_params):
        detail = concept_warnings[-1] if concept_warnings else "concept constituents unavailable"
        raise RuntimeError(
            f"无法解析 {'/'.join(input_params.concepts)} 的精确成分股，且没有可靠行业代理；"
            f"已停止选股，避免错误放宽到全市场。{detail}"
        )
    rank_filters = _daily_filters(
        input_params,
        config,
        include_symbols=concept_symbols,
        taxonomy_supported=_client_supports_tool_feature(
            client,
            "rank_factor_candidates",
            "industry_taxonomy_v1",
        ),
    )
    payload = await client.rank_factor_candidates(
        universe_index=input_params.universe_index,
        trade_date=input_params.trade_date,
        universe_indices=(
            _shadow_universe_indices(input_params, config)
            if _client_supports_tool_feature(
                client, "rank_factor_candidates", "multi_index_universe_v1"
            )
            else None
        ),
        style=profile["investment_style"],
        limit=mcp_limit,
        candidate_limit=input_params.candidate_limit,
        factor_profile=_daily_factor_profile(config, profile),
        filters=rank_filters,
        sector_prefs=_merged_sector_prefs(input_params, profile),
        return_factor_snapshot=True,
        enable_decision=True,
        max_per_industry=config.get("daily_pipeline_max_per_industry", 2),
        decision_config=config.get("daily_pipeline_decision_config"),
        progress_callback=progress_callback,
    )
    warnings = [*concept_warnings, *payload_warnings(payload)]
    rows = payload_rows(payload)
    status = (payload or {}).get("status")
    if status == "error":
        message = payload_error_message(payload, "StockManager 量化排名失败")
        relaxed_payload, relaxation_warnings, relaxations = await _retry_explicit_scope_with_relaxations(
            client,
            input_params,
            profile,
            config,
            rank_filters=rank_filters,
            failed_payload=payload,
            progress_callback=progress_callback,
        )
        relaxed_rows = payload_rows(relaxed_payload)
        if relaxed_rows:
            payload = {
                **(relaxed_payload or {}),
                "filter_relaxation": relaxations[-1] if relaxations else {},
                "filter_relaxations": relaxations,
            }
            rows = relaxed_rows
            warnings = [
                *warnings,
                (
                    f"初始显式板块筛选为空（{message}）；"
                    f"分级放宽风格上限后恢复 {len(relaxed_rows)} 个候选。"
                ),
                *relaxation_warnings,
                *payload_warnings(relaxed_payload),
            ]
        elif (input_params.industries or input_params.concepts or input_params.industry_codes) and (
            _is_candidate_empty(payload)
            or _is_candidate_empty(message)
            or relaxations
        ):
            # An explicit concept/industry request that legitimately has no
            # eligible stocks is a valid screening outcome, not a system
            # failure. Never silently widen it to the whole market/industry.
            final_payload = relaxed_payload or payload or {}
            final_message = payload_error_message(final_payload, str(message))
            empty_scope = {
                "reason": "explicit_scope_no_eligible_candidates",
                "message": final_message,
                "industries": list(input_params.industries),
                "concepts": list(input_params.concepts),
                "industry_taxonomy": input_params.industry_taxonomy,
                "industry_level": input_params.industry_level,
                "industry_codes": list(input_params.industry_codes),
                "relaxations_attempted": relaxations,
            }
            payload = {
                **final_payload,
                "status": "success",
                "rows": [],
                "filter_relaxation": relaxations[-1] if relaxations else {},
                "filter_relaxations": relaxations,
                "empty_scope": empty_scope,
            }
            warnings = [
                *warnings,
                str(message),
                *relaxation_warnings,
                *payload_warnings(final_payload),
                (
                    "精确行业范围保持不变："
                    f"{input_params.industry_taxonomy or '名称口径'} "
                    f"{input_params.industry_level or ''} "
                    f"{','.join(input_params.industry_codes) or '/'.join(input_params.industries)}。"
                ),
                (
                    "显式板块范围内仍无符合条件的标的；任务按“完成但无候选”结束。"
                    "未扩大到全市场，也未将概念静默替换为宽泛行业。"
                ),
            ]
            rows = []
        elif _is_universe_empty(payload) or _is_universe_empty(message):
            fallback_payload, fallback_warning = await _rank_candidates_with_previous_universe(
                client,
                input_params,
                profile,
                config,
                rank_filters=rank_filters,
            )
            fallback_rows = payload_rows(fallback_payload)
            if fallback_rows:
                payload = fallback_payload
                rows = fallback_rows
                warnings = [*warnings, str(message), fallback_warning]
            else:
                raise RuntimeError(f"StockManager 候选池不可用：{message}")
        else:
            raise RuntimeError(f"StockManager 量化排名失败：{message}")
    elif not rows:
        message = (payload or {}).get("message") or "StockManager returned no ranked candidates."
        return [], True, [*warnings, str(message)], _quant_meta(payload, input_params, profile)

    # MCP has already applied the exact industry/concept scope and exhausted
    # the auditable relaxation chain.  Do not run the empty result through the
    # local board/name filters: doing so produces misleading diagnostics such
    # as "board filter left 0" or "industry kept 0 of 0", even though the
    # actual funnel became empty inside MCP.
    if not rows and (payload or {}).get("empty_scope"):
        return [], True, warnings, _quant_meta(payload, input_params, profile)

    # Multi-index v2 keeps the formal Top 5 anchored to the core universe.
    # Expansion-only names are exposed as shadow observations and can never
    # silently backfill a missing core candidate.
    shadow_rows: list[dict[str, Any]] = []
    shadow_indices = _shadow_universe_indices(input_params, config)
    if shadow_indices and any("is_core_universe" in row for row in rows):
        shadow_rows = [row for row in rows if row.get("is_core_universe") is False]
        rows = [row for row in rows if row.get("is_core_universe") is not False]
        if shadow_rows:
            warnings.append(
                f"扩展发现池返回 {len(shadow_rows)} 个候选；保持 shadow，不回填核心 Top {input_params.limit}。"
            )

    rows, board_warnings = _filter_rows_by_board(rows, input_params)
    warnings.extend(board_warnings)
    rows = _attach_fine_industry(rows)
    if concept_symbols:
        warnings.append(
            f"Exact concept constituent restriction applied ({'/'.join(input_params.concepts)}); "
            f"MCP whitelist contained {len(concept_symbols)} symbols before universe intersection."
        )
    else:
        rows, restrict_warnings = _restrict_rows_to_industries(rows, input_params)
        warnings.extend(restrict_warnings)
    rows, demote_warnings = _deprioritize_demoted_rows(rows)
    warnings.extend(demote_warnings)
    rows, industry_warnings = _limit_rows_by_industry(rows, input_params, config)
    warnings.extend(industry_warnings)

    candidates = []
    for row in rows[: input_params.limit]:
        candidate = normalize_quant_candidate(row)
        candidate["board"] = board_for_symbol(candidate.get("symbol") or candidate.get("ts_code"))
        if row.get("industry_detail"):
            candidate["industry_detail"] = str(row["industry_detail"])
        _attach_payload_meta(candidate, payload)
        candidates.append(candidate)
    await _fill_latest_prices(client, candidates, input_params.trade_date, warnings, config)
    for candidate in candidates:
        fusion = fuse_candidate_signal(
            candidate, profile["investment_style"], alpha_override=alpha_override
        )
        candidate.update(fusion)
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = candidate_rationale(candidate, include_llm=True)
    shadow_candidates: list[dict[str, Any]] = []
    for row in shadow_rows[: input_params.limit]:
        candidate = normalize_quant_candidate(row)
        candidate["board"] = board_for_symbol(candidate.get("symbol") or candidate.get("ts_code"))
        candidate["shadow_only"] = True
        candidate["decision_stage"] = "expanded_universe_shadow"
        candidate["final_decision"] = "MONITOR"
        _attach_payload_meta(candidate, payload)
        shadow_candidates.append(candidate)
    quant_meta = _quant_meta(payload, input_params, profile)
    quant_meta["shadow_universe_indices"] = shadow_indices
    quant_meta["shadow_candidates"] = shadow_candidates
    return candidates, True, warnings, quant_meta


_RELAXABLE_EXPLICIT_FILTERS: tuple[tuple[str, str], ...] = (
    ("max_market_cap", "市值上限"),
    ("max_price", "股价上限"),
    ("max_pe", "PE 上限"),
    ("max_pb", "PB 上限"),
    ("max_turnover_rate", "换手率上限"),
)


async def _retry_explicit_scope_with_relaxations(
    client: Any,
    input_params: DailyPipelineInput,
    profile: dict[str, Any],
    config: dict[str, Any],
    *,
    rank_filters: dict[str, Any],
    failed_payload: dict[str, Any] | None,
    progress_callback: Any = None,
) -> tuple[dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    """Progressively relax style ceilings for an explicit concept/industry.

    The requested semantic scope and all safety/tradability floors remain
    fixed. Only upper bounds expressing an investment style are removed, one
    at a time, so every fallback is auditable and a broad market result can
    never masquerade as a concept result.
    """
    if not (input_params.industries or input_params.concepts or input_params.industry_codes):
        return None, [], []

    payload = failed_payload
    relaxed_filters = dict(rank_filters)
    warnings: list[str] = []
    relaxations: list[dict[str, Any]] = []
    attempted: set[str] = set()
    labels = dict(_RELAXABLE_EXPLICIT_FILTERS)

    while _is_candidate_empty(payload) or _is_candidate_empty(
        payload_error_message(payload, "")
    ):
        message = payload_error_message(payload, "")
        # Batch-stage failures cannot be caused by max_price; hard-tradability
        # failures have already passed PE/PB/turnover, so try max_price first.
        if "批量预筛" in message:
            preferred = ("max_market_cap", "max_pe", "max_pb", "max_turnover_rate")
        elif "无可交易标的" in message or "过滤后" in message:
            # The MCP hard-filter message is intentionally aggregate.  A
            # narrow max_market_cap/max_pe/max_turnover pool may leave one
            # symbol which is subsequently rejected for price, liquidity or
            # trading state.  Relax all *upper* style ceilings in a stable
            # order instead of stopping after max_price; safety/tradability
            # floors remain untouched.
            preferred = (
                "max_price",
                "max_market_cap",
                "max_pe",
                "max_pb",
                "max_turnover_rate",
            )
        else:
            preferred = tuple(field for field, _label in _RELAXABLE_EXPLICIT_FILTERS)

        field = next(
            (
                candidate
                for candidate in preferred
                if candidate not in attempted
                and (optional_float(relaxed_filters.get(candidate)) or 0) > 0
            ),
            None,
        )
        if field is None:
            break

        attempted.add(field)
        old_value = optional_float(relaxed_filters.get(field))
        relaxed_filters[field] = 0
        relaxation = {
            "reason": "explicit_scope_empty_after_filter",
            "field": field,
            "from": old_value,
            "to": None,
            "trigger": message,
        }
        relaxations.append(relaxation)
        warnings.append(
            f"显式板块与{labels[field]}冲突：已将 {field} 从 {old_value:g} 放宽为不限；"
            "概念/行业、板别、ST、停牌、一字板、最低市值、上市天数和最低流动性保持不变。"
        )

        payload = await client.rank_factor_candidates(
            universe_index=input_params.universe_index,
            trade_date=input_params.trade_date,
            universe_indices=(
                _shadow_universe_indices(input_params, config)
                if _client_supports_tool_feature(
                    client, "rank_factor_candidates", "multi_index_universe_v1"
                )
                else None
            ),
            style=profile["investment_style"],
            limit=_mcp_fetch_limit(input_params),
            candidate_limit=input_params.candidate_limit,
            factor_profile=_daily_factor_profile(config, profile),
            filters=dict(relaxed_filters),
            sector_prefs=_merged_sector_prefs(input_params, profile),
            return_factor_snapshot=True,
            enable_decision=True,
            max_per_industry=config.get("daily_pipeline_max_per_industry", 2),
            decision_config=config.get("daily_pipeline_decision_config"),
            progress_callback=progress_callback,
        )
        if payload_rows(payload):
            break
        if (payload or {}).get("status") != "error":
            break

    return payload, warnings, relaxations


async def _rank_candidates_with_previous_universe(
    client: Any,
    input_params: DailyPipelineInput,
    profile: dict[str, Any],
    config: dict[str, Any] | None = None,
    *,
    rank_filters: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    config = config or {}
    fallback_dates = _fallback_universe_dates(input_params.trade_date)
    for fallback_date in fallback_dates:
        payload = await client.rank_factor_candidates(
            universe_index=input_params.universe_index,
            trade_date=fallback_date,
            universe_indices=(
                _shadow_universe_indices(input_params, config)
                if _client_supports_tool_feature(
                    client, "rank_factor_candidates", "multi_index_universe_v1"
                )
                else None
            ),
            style=profile["investment_style"],
            limit=_mcp_fetch_limit(input_params),
            candidate_limit=input_params.candidate_limit,
            factor_profile=_daily_factor_profile(config, profile),
            filters=rank_filters or _daily_filters(input_params, config),
            sector_prefs=_merged_sector_prefs(input_params, profile),
            return_factor_snapshot=True,
            enable_decision=True,
            max_per_industry=config.get("daily_pipeline_max_per_industry", 2),
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


def _is_universe_empty(message: Any) -> bool:
    text = str(message)
    return "UNIVERSE_EMPTY" in text or "成分股为空" in text


def _is_candidate_empty(value: Any) -> bool:
    text = str(value)
    return _is_universe_empty(value) or any(
        marker in text
        for marker in (
            "FILTER_EMPTY",
            "无候选股票",
            "无候选标的",
            "无可交易标的",
            "预筛后无候选",
            "预筛后无股票",
        )
    )


def _mcp_fetch_limit(input_params: DailyPipelineInput) -> int:
    """Fetch a wider pool so local board/industry filters can still fill the final limit.

    With an industry restriction the pool must be as deep as possible: the MCP
    ranking is market-wide, so a single industry may only hold a handful of
    rows even in the full candidate pool.
    """
    if input_params.industries or input_params.concepts or input_params.industry_codes:
        return input_params.candidate_limit
    return min(input_params.candidate_limit, max(input_params.limit * 8, input_params.limit + 20))


def _daily_factor_profile(config: dict[str, Any], profile: dict[str, Any]) -> str:
    """Resolve the MCP factor contract used by the daily pipeline.

    The v2 name is intentionally configuration-driven so an operator can
    revert to the legacy style profile without code changes while shadow
    evidence accumulates.
    """
    configured = str(config.get("daily_pipeline_factor_profile") or "daily_pipeline_v2").strip()
    if configured:
        return configured
    return factor_profile_for_style(str(profile.get("investment_style") or "medium_term"))


def _shadow_universe_indices(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
) -> list[str]:
    configured = (
        input_params.shadow_universe_indices
        or config.get("daily_pipeline_shadow_universe_indices")
        or []
    )
    core = str(input_params.universe_index).strip().upper()
    return list(dict.fromkeys(
        str(index).strip().upper()
        for index in configured
        if str(index).strip() and str(index).strip().upper() != core
    ))


# Compatibility aliases retained for tests and older imports. Resolution itself
# lives in core.industry_taxonomy so Market and DailyPipeline cannot drift.
_INDUSTRY_L1_SYNONYMS: dict[str, str] = {
    alias: groups[0]
    for alias, groups in INDUSTRY_SELECTION_ALIASES
    if len(groups) == 1
}


def _expand_industry_terms(keyword: str) -> list[str]:
    """Return the keyword plus any SW L1 group it maps to."""
    return expand_industry_selection_terms(keyword)


# SW2021 申万一级行业全集。用于判断行业词能否翻译成 MCP 认识的精确 L1 名：
# MCP 的 filters.include_industries 是精确 set 匹配，混入非 L1 词会把池子筛空。
_SW_L1_NAMES = SW_L1_INDUSTRIES


def _resolve_sw_l1(keyword: str) -> list[str]:
    """Translate one industry word into SW L1 names ([] when unresolvable)."""
    return resolve_sw_l1_industries(keyword)


def _mcp_include_industries(input_params: DailyPipelineInput) -> list[str]:
    """SW L1 names for MCP ``filters.include_industries``.

    Only push the filter down when EVERY requested keyword translates to a
    valid SW L1 name; a partial translation would over-filter at the MCP
    (exact match) while the local substring fallback could still hit. When
    this returns [], filtering stays local via _restrict_rows_to_industries.
    """
    keywords = [str(k).strip() for k in input_params.industries if str(k).strip()]
    if not keywords:
        return []
    resolved: list[str] = []
    for kw in keywords:
        groups = _resolve_sw_l1(kw)
        if not groups:
            return []
        for group in groups:
            if group not in resolved:
                resolved.append(group)
    return resolved


def _daily_filters(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
    *,
    include_symbols: list[str] | None = None,
    taxonomy_supported: bool = False,
) -> dict[str, Any]:
    """MCP filters with exact symbols/codes preferred over Chinese-name proxies."""
    filters = default_filters(
        board_filter=input_params.board_filter,
        exclude_boards=input_params.exclude_boards,
        config=config,
    )
    symbols = list(dict.fromkeys(include_symbols or []))
    if symbols:
        filters.pop("include_industries", None)
        filters.pop("include_industry_codes", None)
        filters.pop("industry_taxonomy", None)
        filters.pop("industry_level", None)
        filters["include_symbols"] = symbols
    elif input_params.industry_codes and input_params.industry_taxonomy and taxonomy_supported:
        filters.pop("include_industries", None)
        filters["industry_taxonomy"] = input_params.industry_taxonomy
        filters["industry_level"] = input_params.industry_level or "L1"
        filters["include_industry_codes"] = list(dict.fromkeys(
            str(code).strip() for code in input_params.industry_codes if str(code).strip()
        ))
    else:
        include = _mcp_include_industries(input_params)
        if include:
            filters["include_industries"] = include
    return filters


def _client_supports_tool_feature(client: Any, tool: str, feature: str) -> bool:
    """Feature detection that remains compatible with lightweight test/legacy clients."""
    supports = getattr(client, "supports_tool_feature", None)
    if not callable(supports):
        return False
    try:
        return bool(supports(tool, feature))
    except Exception:
        return False


def _resolve_concept_symbol_filter(
    input_params: DailyPipelineInput,
) -> tuple[list[str], list[str]]:
    """Resolve every requested concept or atomically fall back to industries."""
    concepts = [str(item).strip() for item in input_params.concepts if str(item).strip()]
    if not concepts:
        return [], []
    from tradingagents.dataflows.akshare_cn_specific import get_concept_constituents

    symbols: list[str] = []
    warnings: list[str] = []
    for concept in concepts:
        payload = get_concept_constituents(concept)
        resolved = [
            str(row.get("ts_code") or "").strip().upper()
            for row in payload.get("rows") or []
            if str(row.get("ts_code") or "").strip()
        ]
        if not resolved:
            fallback = "/".join(_mcp_include_industries(input_params)) or "无可用行业代理"
            warnings.append(
                f"Concept constituents unavailable ({concept}); exact selection disabled, "
                f"fell back to MCP industry scope: {fallback}."
            )
            return [], warnings
        for symbol in resolved:
            if symbol not in symbols:
                symbols.append(symbol)
        warnings.append(
            f"Concept constituents resolved ({concept}): {len(resolved)} current members "
            f"via {payload.get('source') or 'unknown'}; membership snapshot is not point-in-time."
        )
    return symbols, warnings


def _merged_sector_prefs(
    input_params: DailyPipelineInput, profile: dict[str, Any]
) -> list[str]:
    """Explicit industry restriction (plus SW L1 expansion) first, then profile prefs."""
    merged: list[str] = []
    expanded_industries = [
        term for kw in input_params.industries for term in _expand_industry_terms(str(kw).strip())
    ]
    for item in [*expanded_industries, *(profile.get("sector_prefs") or [])]:
        text = str(item).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _attach_fine_industry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate rows with TuShare 细分行业 (``industry_detail``), best-effort.

    MCP 的 industry 字段是申万一级口径；这里额外标注一层更细的
    TuShare 行业（半导体/元器件...）供展示与精细化过滤，拉不到
    映射时静默跳过，不影响既有链路。
    """
    if not rows:
        return rows
    try:
        from tradingagents.dataflows.tushare_common import get_fine_industry_map

        mapping = get_fine_industry_map()
    except Exception:
        return rows
    if not mapping:
        return rows
    for row in rows:
        code = str(row.get("ts_code") or row.get("symbol") or "").strip().upper()
        detail = mapping.get(code)
        if detail:
            row["industry_detail"] = detail
    return rows


def _restrict_rows_to_industries(
    rows: list[dict[str, Any]],
    input_params: DailyPipelineInput,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Hard-filter rows to the requested industry keywords.

    Two tiers: the coarse tier is bidirectional-substring over the keyword
    AND its 申万一级 expansion (the MCP industry field carries SW L1 names,
    so “半导体设备” must also match rows labelled “电子”). The fine tier
    additionally matches the raw keywords against ``industry_detail``
    (TuShare 细分行业); when enough fine matches exist they replace the
    coarse pool, otherwise they are ranked first and backfilled from it.
    Rows without an industry field never match a restriction.
    """
    keywords = [str(k).strip() for k in input_params.industries if str(k).strip()]
    if not keywords:
        return rows, []
    expanded = {kw: _expand_industry_terms(kw) for kw in keywords}
    fine_kept: list[dict[str, Any]] = []
    coarse_kept: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("industry") or row.get("theme") or "").strip()
        if not text or not any(
            term in text or text in term
            for terms in expanded.values()
            for term in terms
        ):
            continue
        detail = str(row.get("industry_detail") or "").strip()
        if detail and any(kw in detail or detail in kw for kw in keywords):
            fine_kept.append(row)
        else:
            coarse_kept.append(row)
    if fine_kept and len(fine_kept) >= input_params.limit:
        kept = fine_kept
    else:
        kept = [*fine_kept, *coarse_kept]
    applied = "; ".join(
        kw if len(terms) == 1 else f"{kw}→{'/'.join(terms[1:])}"
        for kw, terms in expanded.items()
    )
    warnings = [
        f"Industry restriction applied ({applied}); "
        f"kept {len(kept)} of {len(rows)} candidates."
    ]
    if fine_kept and len(fine_kept) >= input_params.limit:
        warnings.append(
            f"Fine-grained industry match ({'/'.join(keywords)}): "
            f"{len(fine_kept)} candidates; dropped {len(coarse_kept)} broader SW L1 rows."
        )
    elif fine_kept:
        warnings.append(
            f"Fine-grained industry match ({'/'.join(keywords)}) found only "
            f"{len(fine_kept)} candidate(s); ranked first, backfilled from the SW L1 pool."
        )
    if not kept:
        warnings.append(
            "No candidates matched the industry restriction; "
            "try a broader keyword or drop the restriction."
        )
    elif len(kept) < input_params.limit:
        warnings.append(
            f"Industry restriction left only {len(kept)} candidates for requested limit "
            f"{input_params.limit}."
        )
    return kept, warnings


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
        board = board_for_symbol(row.get("ts_code") or row.get("symbol"))
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


def _row_demoted(row: dict[str, Any]) -> bool:
    decision = row.get("decision")
    return bool(decision.get("demoted")) if isinstance(decision, dict) else False


def _deprioritize_demoted_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Push MCP hard-gate demoted rows behind clean rows (contract 4.6 constraint 2).

    MCP ranking is decision-agnostic, so a risk-gated symbol can still hold
    rank 1. A stable partition keeps rank order inside each bucket while
    letting demoted rows only fill leftover candidate slots.
    """
    demoted = [row for row in rows if _row_demoted(row)]
    if not demoted:
        return rows, []
    clean = [row for row in rows if not _row_demoted(row)]
    symbols = ", ".join(str(row.get("ts_code") or row.get("symbol") or "?") for row in demoted[:5])
    suffix = " ..." if len(demoted) > 5 else ""
    warnings = [
        f"MCP decision demoted {len(demoted)} candidate(s) ({symbols}{suffix}); "
        "reordered behind non-demoted rows."
    ]
    return [*clean, *demoted], warnings


def _limit_rows_by_industry(
    rows: list[dict[str, Any]],
    input_params: DailyPipelineInput,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    # An explicit industry restriction contradicts the diversity cap: the user
    # wants N stocks from ONE industry, so capping per-industry slots would
    # silently shrink the result below the requested limit.
    if input_params.industries or input_params.concepts or input_params.industry_codes:
        return rows, []
    max_per_industry = int(config.get("daily_pipeline_max_per_industry", 2) or 0)
    if max_per_industry <= 0:
        return rows, []
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for row in rows:
        group = _industry_group(row.get("industry") or row.get("theme") or "")
        if counts.get(group, 0) >= max_per_industry:
            skipped[group] = skipped.get(group, 0) + 1
            continue
        counts[group] = counts.get(group, 0) + 1
        selected.append(row)
        if len(selected) >= input_params.limit:
            break
    warnings: list[str] = []
    if skipped:
        skipped_text = ", ".join(f"{key}:{value}" for key, value in sorted(skipped.items()))
        warnings.append(
            f"Industry slot cap applied; max_per_industry={max_per_industry}; skipped {skipped_text}."
        )
    if len(selected) < input_params.limit and len(rows) >= input_params.limit:
        warnings.append(
            f"Industry cap left only {len(selected)} candidates for requested limit {input_params.limit}; "
            "increase candidate_limit or relax daily_pipeline_max_per_industry."
        )
    return selected, warnings


def _industry_group(value: Any) -> str:
    text = str(value or "综合").strip()
    if not text:
        return "综合"
    groups = {
        "有色": "有色金属",
        "黄金": "有色金属",
        "铜": "有色金属",
        "铝": "有色金属",
        "白酒": "食品饮料",
        "食品": "食品饮料",
        "饮料": "食品饮料",
        "电子": "电子",
        "半导体": "电子",
        "通信": "通信",
        "新能源": "新能源",
        "电池": "新能源",
        "银行": "银行",
        "保险": "非银金融",
        "证券": "非银金融",
    }
    for needle, group in groups.items():
        if needle in text:
            return group
    return text.split()[0].split("/")[0].split("-")[0]


async def _fill_latest_prices(
    client: Any,
    candidates: list[dict[str, Any]],
    trade_date: str,
    warnings: list[str],
    config: dict[str, Any],
) -> None:
    missing = [c for c in candidates if _candidate_price(c) is None]
    if not missing:
        return
    symbols = [str(c.get("symbol") or c.get("ts_code")) for c in missing if c.get("symbol") or c.get("ts_code")]
    if not symbols:
        return
    payload = None
    if hasattr(client, "get_stock_daily"):
        try:
            end_dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
            start_dt = end_dt - timedelta(days=30)
            for start, end in (
                (start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")),
                (start_dt.isoformat(), end_dt.isoformat()),
            ):
                payload = await client.get_stock_daily(symbols, start, end)
                if payload_rows(payload):
                    break
        except Exception as exc:
            warnings.append(f"Latest close enrichment failed: {exc}")
    else:
        warnings.append("Latest close enrichment skipped: MCP client does not expose get_stock_daily.")
    rows = payload_rows(payload)
    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("ts_code") or row.get("symbol") or "").upper()
        if not symbol:
            continue
        previous = latest_by_symbol.get(symbol)
        row_date = str(row.get("trade_date") or row.get("date") or "")
        prev_date = str((previous or {}).get("trade_date") or (previous or {}).get("date") or "")
        if previous is None or row_date >= prev_date:
            latest_by_symbol[symbol] = row
    for candidate in missing:
        symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "").upper()
        row = latest_by_symbol.get(symbol)
        if not row:
            continue
        close = _first_float(row, "close", "Close", "收盘")
        if close is None:
            continue
        candidate["latest_price"] = close
        candidate["close"] = close
        candidate["price_source"] = "stock_daily"
        candidate["price_trade_date"] = str(row.get("trade_date") or row.get("date") or trade_date)
    remaining = [c for c in candidates if _candidate_price(c) is None]
    if remaining and config.get("daily_pipeline_local_price_fallback_enabled", True):
        for candidate in remaining:
            symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "").upper()
            if not symbol:
                continue
            try:
                quote = await latest_close(symbol, config)
            except Exception as exc:
                warnings.append(f"Local latest close fallback failed for {symbol}: {exc}")
                continue
            if quote is None:
                continue
            candidate["latest_price"] = quote.get("close")
            candidate["close"] = quote.get("close")
            candidate["price_source"] = quote.get("source") or "latest_close_fallback"
            candidate["price_trade_date"] = quote.get("trade_date") or trade_date


def _candidate_price(candidate: dict[str, Any]) -> float | None:
    for container in (
        candidate,
        candidate.get("key_metrics") or {},
        candidate.get("factor_snapshot") or {},
    ):
        if isinstance(container, dict):
            price = _first_float(container, "latest_price", "current_price", "close", "Close", "收盘")
            if price is not None:
                return price
    return None


def _first_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _allowed_boards(input_params: DailyPipelineInput) -> set[str] | None:
    if input_params.board_filter == "main_board":
        return {"main"}
    if input_params.board_filter == "dual_growth_only":
        return {"chinext", "star"}
    return None


def _save_candidate_signals(db: Database, run_id: str, trade_date: str, candidates: list[dict[str, Any]]) -> dict[str, int]:
    failed = 0
    for candidate in candidates:
        try:
            db.save_signal(
                signal_id=str(candidate.get("decision_id") or "").removeprefix("signal:") or _signal_id(trade_date, candidate),
                run_id=run_id,
                trade_date=trade_date,
                symbol=str(candidate.get("symbol") or candidate.get("ts_code") or ""),
                name=str(candidate.get("name") or ""),
                signal=str(candidate.get("final_decision") or candidate.get("signal") or "WATCHLIST"),
                final_score=optional_float(candidate.get("final_score")),
                quant_score=optional_float(candidate.get("quant_score")),
                llm_confidence=optional_float(candidate.get("llm_confidence")),
                fusion_mode=str(candidate.get("fusion_mode") or ""),
                payload=candidate,
            )
        except Exception as exc:
            logger.warning("Failed to save daily pipeline signal for %s: %s", candidate.get("symbol"), exc)
            failed += 1
    if failed:
        logger.warning("Daily pipeline signal persistence: %d/%d failed", failed, len(candidates))
    return {"total": len(candidates), "failed": failed}


def _save_reflection_cases(
    db: Database,
    run_id: str,
    trade_date: str,
    candidates: list[dict[str, Any]],
    investment_style: str = "",
) -> dict[str, int]:
    """Create layered reflection cases from daily pipeline candidates."""
    created = 0
    failed = 0
    for candidate in candidates:
        try:
            symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "").strip().upper()
            if not symbol:
                continue
            enroll_reflection_case(
                db,
                source="daily_pipeline",
                source_type="system_signal",
                symbol=symbol,
                name=str(candidate.get("name") or ""),
                signal_date=str(candidate.get("decision_target_date") or trade_date),
                rating_or_decision=candidate.get("final_decision") or candidate.get("signal"),
                source_run_id=run_id,
                snapshot_payload=_reflection_snapshot(candidate, investment_style),
                # daily_pipeline's 3-tier scope: BUY -> decision_grade,
                # WATCHLIST/MONITOR/HOLD_REVIEW -> candidate_pool, else exploratory.
                decision_grade_values=("buy",),
                candidate_pool_values=("watchlist", "monitor", "hold_review"),
                default_scope="exploratory",
            )
            db.update_decision_record(
                f"signal:{_signal_id(trade_date, candidate)}",
                reflection_case_id=f"daily_pipeline:{candidate.get('decision_target_date') or trade_date}:{symbol}",
            )
            created += 1
        except Exception as exc:
            logger.warning("Failed to save reflection case for %s: %s", candidate.get("symbol"), exc)
            failed += 1
    return {"created": created, "failed": failed}


def _signal_id(trade_date: str, candidate: dict[str, Any]) -> str:
    symbol = candidate.get("symbol") or candidate.get("ts_code") or "unknown"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"daily_pipeline:{trade_date}:{symbol}"))


def _reflection_snapshot(
    candidate: dict[str, Any], investment_style: str = ""
) -> dict[str, Any]:
    """Capture the evidence visible at signal time for later causal reflection."""
    keys = [
        "symbol",
        "name",
        "industry",
        "board",
        "rank",
        "quant_score",
        "quant_decision",
        "quant_decision_reason",
        "quant_demoted",
        "quant_demote_reasons",
        "score_confidence",
        "confidence_breakdown",
        "factor_scores",
        "key_metrics",
        "data_coverage",
        "quant_gate_reasons",
        "gate_reasons",
        "llm_review",
        "llm_view",
        "catalyst_strength",
        "risk_assessment",
        "key_catalysts",
        "key_risks",
        "risk_flags",
        "final_decision",
        "signal",
        "decision_stage",
        "display_score",
        "action_plan",
        "position_pct",
        "rationale",
        "strategy_lesson_hits",
        "memory_trace",
        "lesson_adjustment_reason",
        "deep_analysis",
        "selection_alignment",
        "pre_deep_final_decision",
        "pre_deep_action_plan",
        "deep_analysis_review_required",
    ]
    snapshot = {key: candidate.get(key) for key in keys if key in candidate}
    # Record the run's investment style so the prediction scorecard can bucket
    # RankIC / alpha suggestions per style later (see build_scorecard).
    if investment_style:
        snapshot["investment_style"] = investment_style
    snapshot["candidate"] = {key: value for key, value in candidate.items() if key not in {"raw_payload"}}
    return snapshot


def _save_daily_pipeline_artifacts(
    config: dict[str, Any],
    *,
    input_params: DailyPipelineInput,
    report: str,
    candidates: list[dict[str, Any]],
    quant_candidates: list[dict[str, Any]],
    reviewed_candidates: list[dict[str, Any]],
    decision_pack: list[dict[str, Any]],
    warnings: list[str],
    quant_meta: dict[str, Any],
    signal_result: dict[str, int],
    temporal_context: dict[str, Any] | None = None,
) -> None:
    counts = _decision_counts(candidates)
    title = f"每日选股 {input_params.trade_date}"
    subtitle = f"{input_params.universe_index} · {input_params.board_filter} · Top {len(candidates)}"
    summary = (
        f"输出 {len(candidates)} 个候选，BUY {counts.get('BUY', 0)} 个，"
        f"WATCHLIST {counts.get('WATCHLIST', 0)} 个，SKIP {counts.get('SKIP', 0)} 个"
    )
    base_payload = {
        "trade_date": input_params.trade_date,
        "market_asof_date": (temporal_context or {}).get("market_asof_date") or input_params.trade_date,
        "decision_target_date": (temporal_context or {}).get("decision_target_date"),
        "info_cutoff": (temporal_context or {}).get("info_cutoff"),
        "temporal_context": temporal_context or {},
        "universe_index": input_params.universe_index,
        "board_filter": input_params.board_filter,
        "exclude_boards": input_params.exclude_boards,
        "industries": input_params.industries,
        "concepts": input_params.concepts,
        "industry_taxonomy": input_params.industry_taxonomy,
        "industry_level": input_params.industry_level,
        "industry_codes": input_params.industry_codes,
        "warnings": warnings,
        "quant_meta": quant_meta,
        "signal_result": signal_result,
    }
    tags = ["daily_pipeline", input_params.universe_index, input_params.board_filter]
    save_skill_artifact(
        config,
        skill_id="daily_pipeline",
        artifact_type="screening_report",
        title=title,
        subtitle=subtitle,
        subject_type="market",
        subject_id="cn_a",
        subject_name="A股",
        summary=summary,
        content_markdown=report,
        payload={**base_payload, "candidates": candidates},
        tags=tags,
    )
    save_skill_artifact(
        config,
        skill_id="daily_pipeline",
        artifact_type="signal_pack",
        title=f"{title} 信号包",
        subtitle=subtitle,
        subject_type="market",
        subject_id="cn_a",
        subject_name="A股",
        summary=summary,
        payload={**base_payload, "quant_candidates": quant_candidates, "reviewed_candidates": reviewed_candidates},
        tags=[*tags, "signal_pack"],
    )
    save_skill_artifact(
        config,
        skill_id="daily_pipeline",
        artifact_type="decision_pack",
        title=f"{title} 决策包",
        subtitle=subtitle,
        subject_type="market",
        subject_id="cn_a",
        subject_name="A股",
        summary=summary,
        payload={**base_payload, "decision_pack": decision_pack or candidates},
        tags=[*tags, "decision_pack"],
    )


def _decision_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in candidates:
        decision = str(item.get("final_decision") or item.get("signal") or item.get("quant_decision") or "UNKNOWN").upper()
        counts[decision] = counts.get(decision, 0) + 1
    return counts


def _store_decisions_to_memory(
    candidates: list[dict[str, Any]],
    trade_date: str,
    config: dict[str, Any],
) -> None:
    """Persist each candidate decision to TradingMemoryLog for future reflection."""
    try:
        memory = TradingMemoryLog(config)
        for c in candidates:
            symbol = str(c.get("symbol") or c.get("ts_code") or "")
            if not symbol:
                continue
            decision = c.get("final_decision") or c.get("signal") or "WATCHLIST"
            score = c.get("display_score") or c.get("final_score") or c.get("quant_score") or ""
            rationale = c.get("rationale") or ""
            memory.store_decision(
                ticker=symbol,
                trade_date=trade_date,
                final_trade_decision=(
                    f"DailyPipeline: {decision} (score={score}) | {rationale}"
                ),
            )
    except Exception as exc:
        logger.warning("Failed to store daily pipeline decisions to memory: %s", exc)


async def _apply_llm_reviews(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
    profile: dict[str, Any],
    candidates: list[dict[str, Any]],
    strategy_lessons: list[dict[str, Any]] | None = None,
    alpha_override: float | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Delegate to the shared LLM review runner.

    Daily-pipeline-specific config keys and warning wording are preserved; the
    review loop, enrichment, and re-fusion live in
    :mod:`tradingagents.core.candidate_review_runner`.
    """
    enabled = bool(config.get("daily_pipeline_llm_review_enabled", True))
    reviewer = config.get("daily_pipeline_llm_reviewer")
    review_limit = int(config.get("daily_pipeline_llm_review_limit", 5) or 0)
    return await apply_llm_reviews(
        candidates,
        config=config,
        trade_date=input_params.trade_date,
        style=str(profile.get("investment_style") or "medium_term"),
        reviewer=reviewer,
        review_limit=review_limit,
        enabled=enabled,
        strategy_lessons=strategy_lessons,
        enrich=True,
        alpha_override=alpha_override,
        disabled_warning="Daily pipeline LLM review disabled by config.",
        unavailable_warning="Daily pipeline LLM reviewer unavailable; using quant-only fusion.",
    )


async def _apply_deep_analysis(
    input_params: DailyPipelineInput,
    config: dict[str, Any],
    profile: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    """Optionally run the heavyweight StockAnalysisSkill on the Top N candidates.

    Off by default (``daily_pipeline_deep_analysis_enabled``) because it runs the
    full multi-agent graph per stock. When enabled, each analyzed candidate's
    structured conclusion (rating/target_price/confidence/reasons/plan) is written
    back onto the candidate payload under ``deep_analysis`` so the signal row,
    Library artifact, and reflection snapshot all carry the deep view. The prior
    quant+LLM selection is handed off as ``selection_context`` so the deep pass
    reconciles with — rather than re-derives — the pipeline's conclusion.
    """
    enabled = bool(config.get("daily_pipeline_deep_analysis_enabled", False))
    limit = int(config.get("daily_pipeline_deep_analysis_limit", 1) or 0)
    meta: dict[str, Any] = {
        "enabled": enabled,
        "limit": limit,
        "analyzed": 0,
        "failed": 0,
        "symbols": [],
    }
    if not enabled or limit <= 0 or not candidates:
        return [], meta

    analysis_skill = config.get("daily_pipeline_deep_analysis_skill")
    if analysis_skill is None:
        from tradingagents.skills.stock_analysis.skill import StockAnalysisSkill

        analysis_skill = StockAnalysisSkill()

    warnings: list[str] = []
    timeout_seconds = max(
        1.0,
        float(config.get("daily_pipeline_deep_analysis_timeout_seconds", 300.0) or 300.0),
    )
    for candidate in candidates[:limit]:
        symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "").strip()
        if not symbol:
            continue
        selection_context = _selection_context_for(candidate, input_params.trade_date)
        await report_activity(config, f"deep:{symbol}", "深入分析候选股票", "running",
                              detail=f"标的 {symbol}", agent="Deep Analyst")
        try:
            conclusion = await asyncio.wait_for(
                _run_deep_analysis_once(
                    analysis_skill, symbol, input_params.trade_date, selection_context, config
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            await report_activity(config, f"deep:{symbol}", "深入分析候选股票", "failed",
                                  detail=f"标的 {symbol} · 分析超时", agent="Deep Analyst")
            meta["failed"] = int(meta["failed"]) + 1
            warnings.append(
                f"Deep analysis timed out for {symbol} after {timeout_seconds:.0f}s."
            )
            logger.warning(
                "Daily pipeline deep analysis timed out for %s after %.0fs",
                symbol,
                timeout_seconds,
            )
            continue
        except Exception as exc:  # best-effort: one failure must not abort the batch
            await report_activity(config, f"deep:{symbol}", "深入分析候选股票", "failed",
                                  detail=f"标的 {symbol} · 分析未完成", agent="Deep Analyst")
            meta["failed"] = int(meta["failed"]) + 1
            warnings.append(f"Deep analysis failed for {symbol}: {exc}")
            logger.warning("Daily pipeline deep analysis failed for %s: %s", symbol, exc)
            continue
        await report_activity(config, f"deep:{symbol}", "深入分析候选股票",
                              "completed" if conclusion else "failed",
                              detail=f"标的 {symbol}" + ("" if conclusion else " · 未返回分析结论"),
                              agent="Deep Analyst")
        if conclusion:
            alignment = reconcile_selection_analysis(
                selection_context,
                conclusion,
                analysis_date=input_params.trade_date,
            )
            conclusion["selection_alignment"] = alignment
            candidate["deep_analysis"] = conclusion
            candidate["selection_alignment"] = alignment
            _apply_deep_analysis_safety_gate(candidate, alignment)
            meta["analyzed"] = int(meta["analyzed"]) + 1
            meta["symbols"].append(symbol)
    meta["available"] = int(meta["analyzed"]) > 0
    return warnings, meta


async def _run_deep_analysis_once(
    analysis_skill: Any,
    symbol: str,
    trade_date: str,
    selection_context: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Drive one StockAnalysisSkill run to completion and return its conclusion.

    Consumes the skill's event stream and extracts ``structured_conclusion`` from
    the terminal ``skill_complete`` event. Returns ``{}`` when the run yields no
    conclusion.
    """
    from tradingagents.skills.stock_analysis.skill import StockAnalysisInput

    params = StockAnalysisInput(
        ticker=symbol,
        analysis_date=trade_date,
        selection_context=selection_context,
    )
    conclusion: dict[str, Any] = {}
    async for event in analysis_skill.managed_execute(params, config):
        if event.event_type == "skill_progress" and config.get("_activity_progress"):
            payload = {**event.data}
            payload["activity_id"] = f"deep:{symbol}:{payload.get('activity_id') or payload['stage_id']}"
            payload["stage_id"] = payload["activity_id"]
            payload["parent_activity_id"] = f"deep:{symbol}"
            payload["detail"] = f"标的 {symbol} · {payload.get('detail') or ''}".rstrip(" ·")
            await config["_activity_progress"](payload)
        if event.event_type == "skill_complete":
            conclusion = event.data.get("structured_conclusion") or {}
    return conclusion


def _selection_context_for(candidate: dict[str, Any], trade_date: str) -> dict[str, Any]:
    """Build the selection handoff the deep analysis reconciles against.

    Mirrors the fields ``stock_analysis._format_selection_context`` reads, sourced
    from the pipeline's quant+LLM candidate.
    """
    action_plan = candidate.get("action_plan") or {}
    return {
        "final_decision": candidate.get("final_decision") or candidate.get("signal"),
        "display_score": candidate.get("display_score") or candidate.get("final_score"),
        "quant_score": candidate.get("quant_score"),
        "quant_decision": candidate.get("quant_decision"),
        "llm_score": candidate.get("llm_score"),
        "llm_view": candidate.get("llm_view"),
        "catalyst_strength": candidate.get("catalyst_strength"),
        "risk_assessment": candidate.get("risk_assessment"),
        "score_confidence": candidate.get("score_confidence"),
        "factor_scores": candidate.get("factor_scores"),
        "data_coverage": candidate.get("data_coverage"),
        "quant_gate_reasons": candidate.get("quant_gate_reasons"),
        "gate_reasons": candidate.get("gate_reasons"),
        "risk_flags": candidate.get("risk_flags"),
        "key_catalysts": candidate.get("key_catalysts"),
        "key_risks": candidate.get("key_risks"),
        "entry_zone": candidate.get("entry_zone") or action_plan.get("entry_zone"),
        "stop_loss": candidate.get("stop_loss") or action_plan.get("stop_loss"),
        "targets": (
            candidate.get("targets")
            or action_plan.get("targets")
            or action_plan.get("take_profit")
        ),
        "action_plan": action_plan,
        "reasoning": candidate.get("reasoning") or candidate.get("rationale"),
        "trade_date": candidate.get("price_trade_date") or trade_date,
    }


def _apply_deep_analysis_safety_gate(
    candidate: dict[str, Any],
    alignment: dict[str, Any],
) -> None:
    """Prevent a deep-analysis conflict from silently retaining a BUY action.

    Deep analysis may never auto-promote a non-BUY selection.  When it removes
    conviction from an existing BUY, the candidate is deterministically moved
    to HOLD_REVIEW and its executable sizing/levels are suspended.  The prior
    plan is retained for audit and compare replay.
    """
    if not alignment.get("requires_review"):
        return

    current = str(candidate.get("final_decision") or candidate.get("signal") or "").upper()
    candidate["pre_deep_final_decision"] = current or None
    candidate["deep_analysis_review_required"] = True
    reasons = list(candidate.get("gate_reasons") or [])
    marker = f"deep_analysis_conflict:{alignment.get('analysis_rating') or 'unknown'}"
    if marker not in reasons:
        reasons.append(marker)
    candidate["gate_reasons"] = reasons

    # Never promote WATCHLIST/MONITOR/SKIP from a deep LLM pass. They remain in
    # their already-safe state but carry the explicit review marker.
    if current != "BUY":
        return

    candidate["pre_deep_action_plan"] = candidate.get("action_plan")
    candidate["final_decision"] = "HOLD_REVIEW"
    candidate["signal"] = "HOLD_REVIEW"
    candidate["decision_stage"] = "deep_analysis_reconciliation"
    candidate["position_pct"] = 0.0
    candidate["action_plan"] = {
        "entry_condition": "深度分析与选股 BUY 结论不一致，暂停建仓并等待复核",
        "position_pct": 0.0,
        "stop_loss": None,
        "take_profit": None,
    }


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
            "industry_detail": item.get("industry_detail"),
            "board": item.get("board"),
            "rank": item.get("rank"),
            "quant_score": item.get("quant_score"),
            "universe_percentile": item.get("universe_percentile"),
            "quant_decision": item.get("quant_decision"),
            "quant_decision_reason": item.get("quant_decision_reason"),
            "quant_demoted": item.get("quant_demoted"),
            "quant_demote_reasons": item.get("quant_demote_reasons"),
            "score_confidence": item.get("score_confidence"),
            "confidence_breakdown": item.get("confidence_breakdown"),
            "factor_scores": item.get("factor_scores"),
            "key_metrics": item.get("key_metrics"),
            "data_coverage": item.get("data_coverage"),
            "quant_gate_reasons": item.get("quant_gate_reasons"),
            "warnings": item.get("warnings"),
            # 价格与交易计划：前端 TradePlanBlock 依赖这些字段，不透传会
            # 被渲染成“数据源缺失/未生成”。
            "latest_price": item.get("latest_price"),
            "close": item.get("close"),
            "price_trade_date": item.get("price_trade_date"),
            "price_source": item.get("price_source"),
            "entry_zone": item.get("entry_zone"),
            "stop_loss": item.get("stop_loss"),
            "targets": item.get("targets"),
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
                    "strategy_lesson_hits": item.get("strategy_lesson_hits") or [],
                    "lesson_adjustment_reason": item.get("lesson_adjustment_reason"),
                    "reasoning": item.get("reasoning"),
                    "deep_analysis": item.get("deep_analysis"),
                    "selection_alignment": item.get("selection_alignment"),
                    "pre_deep_final_decision": item.get("pre_deep_final_decision"),
                    "deep_analysis_review_required": item.get("deep_analysis_review_required", False),
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
            "quant_demoted": item.get("quant_demoted"),
            "score_confidence": item.get("score_confidence"),
            "final_decision": item.get("final_decision") or item.get("signal"),
            "decision_stage": item.get("decision_stage"),
            "display_score": item.get("display_score", item.get("final_score")),
            "gate_reasons": item.get("gate_reasons") or [],
            "action_plan": item.get("action_plan") or {},
            "position_pct": item.get("position_pct", 0.0),
            "strategy_lesson_hits": item.get("strategy_lesson_hits") or [],
            "memory_trace": item.get("memory_trace") or {},
            "lesson_adjustment_reason": item.get("lesson_adjustment_reason"),
            "decision_id": item.get("decision_id"),
            "deep_analysis": item.get("deep_analysis"),
            "selection_alignment": item.get("selection_alignment"),
            "pre_deep_final_decision": item.get("pre_deep_final_decision"),
            "deep_analysis_review_required": item.get("deep_analysis_review_required", False),
        }
        for item in candidates
    ]


def _load_strategy_lessons(db: Database, as_of_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from tradingagents.core.strategy_memory import load_strategy_lessons

        return load_strategy_lessons(db, as_of_date)
    except Exception as exc:
        logger.warning("Failed to load strategy lessons for daily pipeline: %s", exc)
        return []


def _quant_meta(payload: dict[str, Any] | None, input_params: DailyPipelineInput, profile: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    return {
        "source": FACTOR_DATA_SOURCE if payload else "unavailable",
        "status": payload.get("status"),
        "method": payload.get("method"),
        "as_of_date": payload.get("as_of_date") or input_params.trade_date,
        "factor_profile": payload.get("factor_profile") or factor_profile_for_style(profile["investment_style"]),
        "universe": payload.get("universe") or {},
        "strategy_meta": payload.get("strategy_meta") or {},
        "strategy_meta_available": bool(payload.get("strategy_meta")),
        "selection_meta": payload.get("selection_meta") or {},
        "concentration_meta": payload.get("concentration_meta") or {},
        "filter_relaxation": payload.get("filter_relaxation") or {},
        "filter_relaxations": payload.get("filter_relaxations") or [],
        "empty_scope": payload.get("empty_scope") or {},
    }


async def _render_llm_briefing(
    candidates: list[dict[str, Any]],
    warnings: list[str],
    temporal_context,
    config: dict[str, Any],
) -> str:
    """Generate a 3-5 sentence Chinese market briefing via LLM.

    Best-effort: returns "" on any failure (LLM unavailable, parse error) so
    the report still renders with the template table only. The briefing gives
    the daily pipeline report a narrative layer (market mood / sector highlights
    / risk notes) instead of being a bare data table.
    """
    if not candidates:
        return ""
    try:
        client = create_llm_client(
            provider=config.get("llm_provider", "openai"),
            model=resolve_model(config),
            base_url=config.get("backend_url"), **provider_kwargs(config),
        )
        llm = runtime_model(client.get_llm(), "Daily Briefing", config)
        # Compact candidate summary to keep the prompt small.
        cand_lines = []
        for item in candidates[:10]:
            cand_lines.append(
                f"- {item.get('symbol','?')} {item.get('name','?')} | "
                f"板块:{item.get('board','?')} | 量化:{item.get('quant_decision','?')} | "
                f"最终:{item.get('final_decision', item.get('signal','?'))} | "
                f"行业:{item.get('industry','?')} | 评分:{item.get('display_score', item.get('final_score','?'))}"
            )
        prompt = (
            "你是 A 股市场早报撰写助手。基于下方当日选股候选与警告，写 3-5 句中文早报摘要，"
            "覆盖：整体市场情绪、板块/行业亮点、资金流或催化线索、主要风险提示。"
            "不要罗列个股，要给交易者一个可快速消化的市场画面。不要暴露思维链。\n\n"
            f"数据基准日: {temporal_context.market_asof_date}\n"
            f"候选数: {len(candidates)}\n"
            f"候选摘要:\n" + "\n".join(cand_lines) + "\n"
            + (f"警告: {'; '.join(warnings)}\n" if warnings else "")
            + "\n早报摘要:"
        )
        response = await llm.ainvoke(prompt)
        text = getattr(response, "content", "") or ""
        text = str(text).strip()
        if text:
            return f"## 市场早报摘要\n\n{text}\n"
        return ""
    except Exception as exc:
        logger.warning("LLM briefing generation failed: %s", exc)
        return ""


def _render_report(
    input_params: DailyPipelineInput,
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    mcp_used: bool,
    warnings: list[str],
    temporal_context,
) -> str:
    lines = [
        "## Daily A-share Research Queue",
        "",
        f"- Market as-of date T: **{temporal_context.market_asof_date}**",
        f"- Decision target date T+1: **{temporal_context.decision_target_date}**",
        f"- Information cutoff: **{temporal_context.info_cutoff}**",
        f"- Universe: **{input_params.universe_index}**",
        f"- Board filter: **{input_params.board_filter}**",
        f"- Investment style: **{profile['investment_style']}**",
        f"- Source: **{'StockManager MCP quant ranking' if mcp_used else 'unavailable/demo'}**",
        f"- Factor source: **{FACTOR_DATA_SOURCE if mcp_used else 'demo/unavailable'}**",
    ]
    if input_params.concepts:
        lines.append(f"- Exact concept scope: **{' / '.join(input_params.concepts)}**")
    elif input_params.industries:
        lines.append(f"- Industry scope: **{' / '.join(input_params.industries)}**")
        if input_params.industry_codes and input_params.industry_taxonomy:
            lines.append(
                f"- Industry taxonomy: **{input_params.industry_taxonomy} "
                f"{input_params.industry_level or 'L1'}** "
                f"(`{'`, `'.join(input_params.industry_codes)}`)"
            )
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
