"""Daily A-share selection pipeline."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.candidate_review_runner import apply_llm_reviews
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.persistence import Database
from tradingagents.core.portfolio_prices import latest_close
from tradingagents.core.reflection_enroll import enroll_reflection_case
from tradingagents.core.signal_fusion import fuse_candidate_signal, quant_evidence_markdown
from tradingagents.core.trading_time import get_temporal_context
from tradingagents.dataflows.mcp_adapter import (
    normalize_quant_candidate,
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
    factor_profile_for_style,
    optional_float,
    resolve_board_filter,
    resolve_temporal_context,
)
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress

logger = logging.getLogger(__name__)


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
        raw_trade_date = input_params.trade_date
        temporal_context, _ = resolve_temporal_context(
            config, raw_trade_date, market="cn_a", date_field="trade_date"
        )
        input_params = _apply_runtime_defaults(input_params, config, temporal_context)
        db = config.get("db") or Database()
        profile = db.get_user_profile()
        strategy_lessons = _load_strategy_lessons(db)

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
        review_warnings, review_meta = await _apply_llm_reviews(
            input_params,
            config,
            profile,
            candidates,
            strategy_lessons=strategy_lessons,
        )
        yield skill_progress(
            stage_id="llm_review",
            stage_label="LLM 候选复核",
            status="completed",
            detail=f"复核状态: {'可用' if review_meta.get('available') else '降级'}",
            agent="LLM Reviewer",
            progress_pct=78,
        )
        warnings = [*warnings, *review_warnings]
        deep_warnings, deep_meta = await _apply_deep_analysis(
            input_params,
            config,
            profile,
            candidates,
        )
        warnings = [*warnings, *deep_warnings]
        if deep_meta.get("enabled"):
            yield skill_progress(
                stage_id="deep_analysis",
                stage_label="Top N 深度分析",
                status="completed",
                detail=(
                    f"深度分析 {deep_meta.get('analyzed', 0)} 只，"
                    f"失败 {deep_meta.get('failed', 0)} 只"
                ),
                agent="Deep Analyst",
                progress_pct=84,
            )
        strategy_meta = quant_meta.get("strategy_meta") or {}
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
        briefing = await _render_llm_briefing(candidates, warnings, temporal_context, config)
        report = _render_report(input_params, candidates, profile, mcp_used, warnings, temporal_context)
        if briefing:
            report = briefing + "\n" + report
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
                "profile": profile,
                "mcp_used": mcp_used,
                "quant_meta": quant_meta,
                "strategy_meta": strategy_meta,
                "review_meta": review_meta,
                "deep_meta": deep_meta,
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
) -> tuple[list[dict[str, Any]], bool, list[str], dict[str, Any]]:
    client = await get_mcp_client(config)
    if client is None:
        if config.get("daily_pipeline_demo_fallback", False):
            return demo_candidates(input_params.limit, profile["investment_style"], include_board=True), False, [
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
        factor_profile=factor_profile_for_style(profile["investment_style"]),
        filters=default_filters(
            board_filter=input_params.board_filter,
            exclude_boards=input_params.exclude_boards,
            config=config,
        ),
        sector_prefs=profile.get("sector_prefs") or [],
        return_factor_snapshot=True,
        enable_decision=True,
        max_per_industry=config.get("daily_pipeline_max_per_industry", 2),
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
    rows, industry_warnings = _limit_rows_by_industry(rows, input_params, config)
    warnings.extend(industry_warnings)

    candidates = []
    for row in rows[: input_params.limit]:
        candidate = normalize_quant_candidate(row)
        candidate["board"] = board_for_symbol(candidate.get("symbol") or candidate.get("ts_code"))
        _attach_payload_meta(candidate, payload)
        candidates.append(candidate)
    await _fill_latest_prices(client, candidates, input_params.trade_date, warnings, config)
    for candidate in candidates:
        fusion = fuse_candidate_signal(candidate, profile["investment_style"])
        candidate.update(fusion)
        candidate["quant_evidence"] = quant_evidence_markdown(candidate)
        candidate["rationale"] = candidate_rationale(candidate, include_llm=True)
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
            factor_profile=factor_profile_for_style(profile["investment_style"]),
            filters=default_filters(
                board_filter=input_params.board_filter,
                exclude_boards=input_params.exclude_boards,
                config=config,
            ),
            sector_prefs=profile.get("sector_prefs") or [],
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


def _mcp_fetch_limit(input_params: DailyPipelineInput) -> int:
    """Fetch a wider pool so local board/industry filters can still fill the final limit."""
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


def _limit_rows_by_industry(
    rows: list[dict[str, Any]],
    input_params: DailyPipelineInput,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
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
                snapshot_payload=_reflection_snapshot(candidate),
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


def _reflection_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
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
        "lesson_adjustment_reason",
        "deep_analysis",
    ]
    snapshot = {key: candidate.get(key) for key in keys if key in candidate}
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
    for candidate in candidates[:limit]:
        symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "").strip()
        if not symbol:
            continue
        selection_context = _selection_context_for(candidate, input_params.trade_date)
        try:
            conclusion = await _run_deep_analysis_once(
                analysis_skill, symbol, input_params.trade_date, selection_context, config
            )
        except Exception as exc:  # best-effort: one failure must not abort the batch
            meta["failed"] = int(meta["failed"]) + 1
            warnings.append(f"Deep analysis failed for {symbol}: {exc}")
            logger.warning("Daily pipeline deep analysis failed for %s: %s", symbol, exc)
            continue
        if conclusion:
            candidate["deep_analysis"] = conclusion
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
    async for event in analysis_skill.execute(params, config):
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
        "entry_zone": action_plan.get("entry_zone"),
        "stop_loss": action_plan.get("stop_loss"),
        "targets": action_plan.get("targets"),
        "action_plan": action_plan,
        "reasoning": candidate.get("reasoning") or candidate.get("rationale"),
        "trade_date": candidate.get("price_trade_date") or trade_date,
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
                    "strategy_lesson_hits": item.get("strategy_lesson_hits") or [],
                    "lesson_adjustment_reason": item.get("lesson_adjustment_reason"),
                    "reasoning": item.get("reasoning"),
                    "deep_analysis": item.get("deep_analysis"),
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
            "strategy_lesson_hits": item.get("strategy_lesson_hits") or [],
            "lesson_adjustment_reason": item.get("lesson_adjustment_reason"),
            "decision_id": item.get("decision_id"),
            "deep_analysis": item.get("deep_analysis"),
        }
        for item in candidates
    ]


def _load_strategy_lessons(db: Database) -> list[dict[str, Any]]:
    try:
        return db.list_strategy_lessons(limit=20, active_only=True)
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
            model=config.get("quick_think_llm", "gpt-5.4-mini"),
            base_url=config.get("backend_url"),
        )
        llm = client.get_llm()
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
