"""After-close daily review workflow."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.persistence import Database
from tradingagents.core.portfolio_prices import latest_close
from tradingagents.core.reflection import ReflectionEngine
from tradingagents.core.trading_time import get_temporal_context
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress
from tradingagents.skills._shared import resolve_board_filter
from tradingagents.skills.daily_pipeline.skill import DailyPipelineInput, skill as daily_pipeline_skill
from tradingagents.skills.risk_monitor.skill import RiskMonitorInput, skill as risk_monitor_skill

logger = logging.getLogger(__name__)


class DailyReviewInput(BaseModel):
    trade_date: str = Field(default_factory=lambda: date.today().isoformat())
    daily_limit: int = Field(default=5, ge=1, le=20)
    candidate_limit: int = Field(default=80, ge=5, le=800)
    board_filter: str = Field(default="all")
    risk_lookback_days: int = Field(default=30, ge=1, le=365)
    reflection_lookback_days: int = Field(default=30, ge=1, le=365)


class DailyReviewOutput(BaseModel):
    trade_date: str
    refreshed_prices: dict[str, Any]
    reflection: dict[str, Any]
    next_day_plan: dict[str, Any]


class DailyReviewSkill(BaseSkill):
    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="daily_review",
            name="Daily Review",
            description="After-close cockpit workflow: refresh holdings, scan risk, reflect outcomes, and prepare next-day plan.",
            version="1.0.0",
            triggers=["收盘复盘", "每日复盘", "次日计划", "daily review", "after close"],
            icon="clipboard-check",
            category="workflow",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return DailyReviewInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return DailyReviewOutput

    async def execute(self, params: BaseModel, config: dict[str, Any]) -> AsyncIterator[SkillEvent]:
        input_params: DailyReviewInput = params
        raw_trade_date = input_params.trade_date
        current_temporal_context = get_temporal_context(config, market="cn_a")
        is_current_default = raw_trade_date in {date.today().isoformat(), current_temporal_context.now[:10]}
        temporal_context = (
            current_temporal_context
            if is_current_default
            else get_temporal_context(config, market="cn_a", requested_date=input_params.trade_date)
        )
        if input_params.trade_date != temporal_context.market_asof_date:
            input_params = input_params.model_copy(update={"trade_date": temporal_context.market_asof_date})
        db = config.get("db") or Database()
        run_id = str(config.get("run_id", ""))

        yield SkillEvent(
            event_type="skill_start",
            data={
                "skill_id": self.metadata.id,
                "trade_date": input_params.trade_date,
                "temporal_context": temporal_context.to_dict(),
            },
        )
        yield skill_progress(
            stage_id="refresh_prices",
            stage_label="刷新持仓收盘价",
            status="running",
            detail="正在读取最新收盘价并重算持仓盈亏",
            progress_pct=10,
        )
        refreshed = await _refresh_holding_prices(db, config)
        yield skill_progress(
            stage_id="refresh_prices",
            stage_label="刷新持仓收盘价",
            status="completed",
            detail=f"更新 {refreshed['updated']} 个持仓，失败 {len(refreshed['failed'])} 个",
            progress_pct=20,
        )

        yield skill_progress(
            stage_id="risk_monitor",
            stage_label="扫描持仓风险",
            status="running",
            detail=f"回看 {input_params.risk_lookback_days} 天公告与风险事件",
            progress_pct=30,
        )
        risk_result: dict[str, Any] = {}
        async for event in risk_monitor_skill.execute(
            RiskMonitorInput(lookback_days=input_params.risk_lookback_days),
            config,
        ):
            if event.event_type == "skill_complete":
                risk_result = event.data
            elif event.event_type in {"risk_monitor_results", "report_chunk"}:
                yield event
        yield skill_progress(
            stage_id="risk_monitor",
            stage_label="扫描持仓风险",
            status="completed",
            detail=f"发现 {len(risk_result.get('risks') or [])} 条风险记录",
            progress_pct=45,
        )

        yield skill_progress(
            stage_id="reflection",
            stage_label="因果反思批处理",
            status="running",
            detail="正在处理到期信号并生成策略经验",
            progress_pct=55,
        )
        reflection_result = await _run_reflection(db, config, input_params.reflection_lookback_days)
        yield skill_progress(
            stage_id="reflection",
            stage_label="因果反思批处理",
            status="completed",
            detail=f"处理 {reflection_result.get('processed', 0)} 条，经验 {reflection_result.get('lessons_created', 0)} 条",
            progress_pct=65,
        )

        # Resolve the effective board_filter the same way daily_pipeline does
        # (explicit input > Dashboard FilterPanel > env > "all") so daily_review
        # honors the global filter setting, then pass it through to daily_pipeline.
        effective_board_filter = resolve_board_filter(input_params.board_filter, config)

        yield skill_progress(
            stage_id="daily_pipeline",
            stage_label="生成次日选股候选",
            status="running",
            detail=f"Top {input_params.daily_limit} · {effective_board_filter}",
            progress_pct=75,
        )
        daily_result: dict[str, Any] = {}
        async for event in daily_pipeline_skill.execute(
            DailyPipelineInput(
                trade_date=input_params.trade_date,
                limit=input_params.daily_limit,
                candidate_limit=input_params.candidate_limit,
                board_filter=effective_board_filter,
            ),
            config,
        ):
            if event.event_type == "skill_complete":
                daily_result = event.data
            elif event.event_type in {"daily_pipeline_candidates", "report_chunk"}:
                yield event
        yield skill_progress(
            stage_id="daily_pipeline",
            stage_label="生成次日选股候选",
            status="completed",
            detail=f"输出 {len(daily_result.get('candidates') or [])} 个候选",
            progress_pct=88,
        )

        next_day_plan = _build_next_day_plan(db, input_params, refreshed, risk_result, reflection_result, daily_result)
        next_day_plan["market_asof_date"] = temporal_context.market_asof_date
        next_day_plan["decision_target_date"] = temporal_context.decision_target_date
        next_day_plan["info_cutoff"] = temporal_context.info_cutoff
        next_day_plan["temporal_context"] = temporal_context.to_dict()
        report = _render_daily_review_report(next_day_plan)
        save_skill_artifact(
            config,
            skill_id="daily_review",
            artifact_type="daily_review_report",
            title=f"收盘复盘 {input_params.trade_date}",
            subtitle="持仓 · 风险 · 反思 · 选股",
            subject_type="system",
            subject_id="daily_review",
            subject_name="收盘复盘",
            summary=next_day_plan["summary"],
            content_markdown=report,
            payload=next_day_plan,
            tags=["daily_review", input_params.trade_date],
        )
        save_skill_artifact(
            config,
            skill_id="daily_review",
            artifact_type="next_day_plan",
            title=f"次日计划 {input_params.trade_date}",
            subtitle=f"BUY {next_day_plan['candidate_counts'].get('BUY', 0)} · WATCHLIST {next_day_plan['candidate_counts'].get('WATCHLIST', 0)}",
            subject_type="system",
            subject_id="next_day_plan",
            subject_name="次日计划",
            summary=next_day_plan["summary"],
            content_markdown=report,
            payload=next_day_plan,
            tags=["next_day_plan", input_params.trade_date],
        )
        yield SkillEvent(
            event_type="report_chunk",
            data={"section": "daily_review_report", "content": report, "is_final": True},
        )
        yield skill_progress(
            stage_id="next_day_plan",
            stage_label="生成次日计划",
            status="completed",
            detail="次日计划已写入 Library",
            progress_pct=100,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "trade_date": input_params.trade_date,
                "market_asof_date": temporal_context.market_asof_date,
                "decision_target_date": temporal_context.decision_target_date,
                "info_cutoff": temporal_context.info_cutoff,
                "temporal_context": temporal_context.to_dict(),
                "run_id": run_id,
                "refreshed_prices": refreshed,
                "reflection": reflection_result,
                "next_day_plan": next_day_plan,
            },
        )

    async def cancel(self) -> None:
        return None


async def _refresh_holding_prices(db: Database, config: dict[str, Any]) -> dict[str, Any]:
    holdings = db.list_holdings()
    updated = 0
    failed: list[dict[str, str]] = []
    for holding in holdings:
        symbol = str(holding.get("symbol") or "")
        try:
            quote = await latest_close(symbol, config)
            if quote is None:
                failed.append({"symbol": symbol, "reason": "latest close unavailable"})
                continue
            # Update only the price column — upsert_holding would rewrite
            # quantity/avg_cost/notes and race with concurrent user edits
            # (same rationale as the /portfolio/refresh-prices route).
            db.update_holding_price(
                symbol=symbol,
                current_price=float(quote["close"]),
            )
            updated += 1
        except Exception as exc:
            failed.append({"symbol": symbol, "reason": str(exc)})
    return {"updated": updated, "failed": failed, "holdings": db.list_holdings()}


async def _run_reflection(db: Database, config: dict[str, Any], lookback_days: int) -> dict[str, Any]:
    try:
        engine = ReflectionEngine(
            db=db,
            config=config,
            memory_log=TradingMemoryLog(config),
        )
        return await engine.run_reflection_batch(lookback_days=lookback_days)
    except Exception as exc:
        logger.warning("Daily review reflection failed: %s", exc)
        return {"processed": 0, "errors": 1, "error": str(exc)}


def _build_next_day_plan(
    db: Database,
    input_params: DailyReviewInput,
    refreshed: dict[str, Any],
    risk_result: dict[str, Any],
    reflection_result: dict[str, Any],
    daily_result: dict[str, Any],
) -> dict[str, Any]:
    candidates = daily_result.get("candidates") or []
    risks = risk_result.get("risks") or []
    high_risks = [item for item in risks if str(item.get("level") or "").lower() in {"red", "critical", "high"}]
    counts: dict[str, int] = {}
    for candidate in candidates:
        decision = str(candidate.get("final_decision") or candidate.get("signal") or "UNKNOWN").upper()
        counts[decision] = counts.get(decision, 0) + 1
    lessons = db.list_strategy_lessons(limit=5, active_only=True)
    summary = (
        f"刷新 {refreshed.get('updated', 0)} 个持仓，发现高风险 {len(high_risks)} 个，"
        f"反思 {reflection_result.get('processed', 0)} 条，输出候选 {len(candidates)} 个"
    )
    return {
        "trade_date": input_params.trade_date,
        "summary": summary,
        "refreshed_prices": refreshed,
        "risk_items": risks,
        "high_risk_items": high_risks,
        "reflection": reflection_result,
        "active_strategy_lessons": lessons,
        "candidates": candidates,
        "candidate_counts": counts,
        "next_actions": _next_actions(candidates, high_risks, lessons),
    }


def _next_actions(candidates: list[dict[str, Any]], high_risks: list[dict[str, Any]], lessons: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for item in high_risks[:3]:
        actions.append({"type": "risk", "label": f"复核持仓风险 {item.get('symbol')}", "symbol": str(item.get("symbol") or "")})
    for item in candidates:
        if str(item.get("final_decision") or item.get("signal") or "").upper() == "BUY":
            actions.append({"type": "buy_candidate", "label": f"深度分析买入候选 {item.get('symbol')}", "symbol": str(item.get("symbol") or "")})
    if lessons:
        actions.append({"type": "reflection", "label": f"查看 {len(lessons)} 条活跃策略经验", "symbol": ""})
    return actions[:6]


def _render_daily_review_report(plan: dict[str, Any]) -> str:
    lines = [
        "# 收盘复盘与次日计划",
        "",
        f"- 行情基准日 T: **{plan.get('market_asof_date') or plan.get('trade_date')}**",
        f"- 次日计划目标: **{plan.get('decision_target_date') or '-'}**",
        f"- 信息截止: **{plan.get('info_cutoff') or '-'}**",
        f"- 摘要: {plan.get('summary')}",
        "",
        "## 次日动作",
    ]
    actions = plan.get("next_actions") or []
    if not actions:
        lines.append("- 暂无强制动作。")
    for action in actions:
        lines.append(f"- {action.get('label')}")
    lines.extend(["", "## 活跃策略经验"])
    lessons = plan.get("active_strategy_lessons") or []
    if not lessons:
        lines.append("- 暂无活跃策略经验。")
    for lesson in lessons:
        lines.append(f"- [{lesson.get('confidence')}] {lesson.get('finding')}")
    lines.extend(["", "## 候选统计"])
    for decision, count in (plan.get("candidate_counts") or {}).items():
        lines.append(f"- {decision}: {count}")
    return "\n".join(lines)


skill = DailyReviewSkill()
