"""Stock analysis skill — wraps the existing TradingAgentsGraph.

This is the primary skill that exposes the multi-agent stock analysis
pipeline: 13 agents across 5 phases (Analyst → Research → Trader →
Risk → Portfolio Manager).
"""

import logging
import re
from collections.abc import AsyncIterator
from datetime import date, datetime as _dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from tradingagents.core.activity_labels import (
    AGENT_ACTIONS,
    activity_detail,
    activity_message,
    tool_action,
)
from tradingagents.core.agent_runtime import AgentSpec, bind_scope, managed_stream
from tradingagents.core.decision_reconciliation import reconcile_selection_analysis
from tradingagents.core.reflection_enroll import enroll_reflection_case
from tradingagents.core.research_context import (
    REPORT_ROLES,
    load_reusable_research,
    research_policy_key,
)
from tradingagents.core.strategy_memory import (
    lesson_prompt_section,
    load_strategy_lessons,
    select_strategy_lessons,
    validate_memory_usage,
)
from tradingagents.dataflows.symbol_utils import detect_market
from tradingagents.skills._shared import optional_float, resolve_temporal_context
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress

logger = logging.getLogger(__name__)

AGENT_PROGRESS_STAGES: dict[str, tuple[str, str]] = {
    "Market Analyst": ("analyst_market", "市场分析"),
    "Sentiment Analyst": ("analyst_sentiment", "情绪分析"),
    "News Analyst": ("analyst_news", "新闻与公告分析"),
    "Fundamentals Analyst": ("analyst_fundamentals", "基本面分析"),
    "Bull Researcher": ("research_bull", "多方观点"),
    "Bear Researcher": ("research_bear", "空方观点"),
    "Research Manager": ("research_manager", "研究经理裁决"),
    "Trader": ("trader_plan", "交易计划"),
    "Aggressive Analyst": ("risk_aggressive", "激进风控观点"),
    "Conservative Analyst": ("risk_conservative", "保守风控观点"),
    "Neutral Analyst": ("risk_neutral", "中性风控观点"),
    "Portfolio Manager": ("portfolio_decision", "组合经理决策"),
}


class StockAnalysisInput(BaseModel):
    """Input parameters for stock analysis."""

    ticker: str = Field(
        description="Stock ticker symbol, e.g. 'AAPL' or '600519.SS' for A-shares"
    )
    analysis_date: str = Field(
        default_factory=lambda: date.today().isoformat(),
        description="Analysis date in YYYY-MM-DD format",
    )
    analysts: list[Literal["market", "social", "news", "fundamentals"]] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"],
        min_length=1,
        description=("Only these analyst keys are supported: market (price/technicals), "
                     "social (sentiment), news (news/events), fundamentals "
                     "(business/industry, revenue, profitability, financial statements, "
                     "cash flow and valuation). Industry is covered by fundamentals; "
                     "there is no separate industry analyst."),
    )
    analysis_template: str = Field(default="full", pattern="^(full|research)$", description="full includes debate and risk; research only runs selected analysts")
    force_refresh: bool = Field(default=False, description="Fetch and analyze again instead of reusing valid research in this conversation. Set true when the user explicitly requests refreshed data or a fresh analysis.")
    debate_rounds: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Number of bull/bear debate rounds",
    )
    risk_rounds: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Number of risk debate rounds",
    )
    asset_type: str = Field(
        default="stock",
        description="Asset type: 'stock' or 'crypto'",
    )
    reflection_context: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Recent reflection lessons injected by the orchestrator.",
    )
    include_portfolio_context: bool = Field(
        default=False,
        description="Opt in only when the user requests a review of locally tracked holdings. Ordinary stock research must not assume cached holdings are current.",
    )
    holding_context: dict[str, Any] | None = Field(
        default=None,
        description="Optional explicit holding context for this ticker.",
    )
    selection_context: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional structured plan handed off from the market scanner / daily "
            "pipeline (entry_zone, stop_loss, targets, action_plan, reasoning, "
            "final_decision, score, trade_date). Injected into the agent context "
            "so the analysis is anchored to — and can explicitly reconcile with — "
            "the selection's conclusion instead of re-analyzing from scratch."
        ),
    )


class StockAnalysisOutput(BaseModel):
    """Output from stock analysis."""

    ticker: str
    report_path: str | None = Field(default=None, description="Path to saved report")
    artifact_id: str
    holding_context: dict[str, Any] | None = None
    selection_context: dict[str, Any] | None = None
    structured_conclusion: dict[str, Any]
    specialist_results: list[dict[str, Any]] = Field(default_factory=list)


class StockAnalysisSkill(BaseSkill):
    """Wraps TradingAgentsGraph as a pluggable Skill."""

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="stock_analysis",
            name="Stock Analysis",
            description=(
                "Multi-agent stock analysis with bull/bear debate, risk assessment, "
                "and portfolio management decision. Supports US and A-share markets."
            ),
            version="1.0.0",
            triggers=["分析", "analyze", "看看", "怎么样", "研报", "report", "股票", "stock"],
            icon="chart-line",
            category="analysis",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return StockAnalysisInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return StockAnalysisOutput

    async def execute(self, params, config):
        async for event in managed_stream(AgentSpec("stock_analysis"), self._execute(params, config), config):
            yield event

    async def _execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        """Execute stock analysis, yielding events for real-time streaming."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        input_params: StockAnalysisInput = params
        raw_analysis_date = input_params.analysis_date
        market = detect_market(input_params.ticker)
        temporal_context, input_params = resolve_temporal_context(
            config, raw_analysis_date, market=market, date_field="analysis_date", params=input_params
        )

        db = config.get("db")
        holding_context = input_params.holding_context
        if holding_context is None and input_params.include_portfolio_context:
            holding_context = await _load_holding_context(
                db,
                input_params.ticker,
                config,
            )
        elif holding_context is not None:
            # Frontend handed off raw holding fields (e.g. from the portfolio
            # UI); enrich them with derived metrics so the explicit path
            # matches the auto-loaded one instead of only carrying raw fields.
            holding_context = await _load_holding_context(
                db,
                input_params.ticker,
                config,
                holding=holding_context,
            )

        bind_scope(research_reuse_allowed=not bool(
            holding_context or input_params.include_portfolio_context or input_params.selection_context or input_params.reflection_context))

        selected_memory = []
        memory_error = None
        try:
            selected_memory = select_strategy_lessons(
                load_strategy_lessons(db, temporal_context.market_asof_date), {
                    "symbol": input_params.ticker,
                    "industry": (input_params.selection_context or {}).get("industry") or
                                (holding_context or {}).get("industry"),
                    "style": config.get("investment_style", "medium_term"),
                }, as_of_date=temporal_context.market_asof_date,
            )
        except Exception as exc:
            memory_error = "历史经验检索不可用，本轮未注入策略经验"
            logger.warning("Stock analysis memory unavailable: %s", exc)
        bind_scope(symbols=[input_params.ticker], as_of_date=temporal_context.market_asof_date,
                   info_cutoff=temporal_context.info_cutoff,
                   memory_refs=[row["id"] for row in selected_memory], memory_prompt=lesson_prompt_section(selected_memory))
        memory_context = _join_context_blocks(
            _format_holding_context(holding_context),
            _format_reflection_context(input_params.reflection_context),
            _format_selection_context(input_params.selection_context),
            lesson_prompt_section(selected_memory),
        )

        run_config = {
            **config,
            "max_debate_rounds": input_params.debate_rounds,
            "max_risk_discuss_rounds": input_params.risk_rounds,
            "memory_extra_context": memory_context,
            "analysis_template": input_params.analysis_template,
            "holding_context": holding_context,
            "temporal_context": temporal_context.to_dict(),
            "market_asof_date": temporal_context.market_asof_date,
            "info_cutoff": temporal_context.info_cutoff,
        }

        reusable = {}
        if not (input_params.force_refresh or config.get("research_force_refresh") or
                holding_context or input_params.include_portfolio_context or input_params.selection_context or input_params.reflection_context):
            try:
                reusable = load_reusable_research(
                    db, config.get("research_conversation_id"), config.get("research_task_id"),
                    symbol=input_params.ticker.upper().replace(".SS", ".SH"),
                    as_of_date=temporal_context.market_asof_date, info_cutoff=temporal_context.info_cutoff,
                    policy_key=research_policy_key(config, input_params.asset_type, lesson_prompt_section(selected_memory)),
                )
            except Exception:
                logger.warning("Research reuse unavailable; running fresh analysis", exc_info=True)
        reusable = {key: value for key, value in reusable.items() if REPORT_ROLES[key][0] in input_params.analysts}
        run_config["research_reuse"] = reusable

        yield SkillEvent(
            event_type="skill_start",
            data={
                "skill_id": "stock_analysis",
                "ticker": input_params.ticker,
                "date": input_params.analysis_date,
                "market_asof_date": temporal_context.market_asof_date,
                "info_cutoff": temporal_context.info_cutoff,
                "temporal_context": temporal_context.to_dict(),
                "holding_context_available": bool(holding_context),
                "selection_context_available": bool(input_params.selection_context),
            },
        )
        yield skill_progress(
            stage_id="strategy_memory", stage_label="核对历史经验", status="completed",
            detail=memory_error or (f"提供 {len(selected_memory)} 条适用经验供分析参考，使用情况以报告为准"
                                   if selected_memory else "未找到适用的已批准经验，依据当前证据分析"),
            data={"memory_trace": {"snapshots": selected_memory, "as_of_date": temporal_context.market_asof_date,
                                   "status": "provided_to_analysis"}},
        )
        yield skill_progress(
            stage_id="prepare",
            stage_label="准备分析",
            status="completed",
            detail=(
                f"{input_params.ticker} · {input_params.analysis_date}"
                + (" · 已注入持仓上下文" if holding_context else "")
            ),
            progress_pct=5,
            data={"holding_context": holding_context} if holding_context else None,
        )
        reused_labels = [REPORT_ROLES[key][2] for key in reusable]
        fresh_labels = [label for key, (analyst, _, label) in REPORT_ROLES.items()
                        if analyst in input_params.analysts and key not in reusable]
        yield skill_progress(
            stage_id="research_scope", stage_label="核对已有研究", status="completed",
            detail=(("复用有效的" + "、".join(reused_labels) + "研究；") if reused_labels else "没有可复用的有效研究；")
                   + (("本轮获取" + "、".join(fresh_labels) + "数据") if fresh_labels else "所选研究已覆盖")
                   + ("，继续研究裁决与风控" if input_params.analysis_template == "full" else "，仅执行所选研究维度"),
            data={"reused": reused_labels, "fresh": fresh_labels,
                  "sources": [value["source"] for value in reusable.values()]},
        )

        ta = TradingAgentsGraph(
            selected_analysts=input_params.analysts,
            config=run_config,
        )

        report_path = None
        report_sections: dict[str, str] = {}
        structured_portfolio_decision: dict[str, Any] | None = None
        specialist_results = []

        async for event in ta.astream_propagate(
            ticker=input_params.ticker,
            date=input_params.analysis_date,
            selected_analysts=input_params.analysts,
            asset_type=input_params.asset_type,
        ):
            event_type = event["type"]
            event_data = event["data"]
            if event_type == "context_compacted":
                yield skill_progress(
                    stage_id="handoff_" + str(event_data.get("agent", "research")).lower().replace(" ", "_"),
                    stage_label="整理研究摘要", status="completed",
                    detail="保留事实、判断、风险、数据缺口和来源，完整报告仍可查看",
                    data={"context_stats": event_data.get("context_stats")},
                )
                continue
            if event_type == "agent_status":
                agent = str(event_data.get("agent") or "")
                status = str(event_data.get("status") or "running")
                stage_id, stage_label = AGENT_PROGRESS_STAGES.get(
                    agent,
                    ("agent_work", agent or "Agent 执行"),
                )
                yield skill_progress(
                    stage_id=stage_id,
                    stage_label=stage_label,
                    status=status if status in {"completed", "failed"} else "running",
                    activity_id=event_data.get("activity_id"),
                    step_id=agent.lower().replace(" ", "_") if agent else None,
                    step_label=activity_message(AGENT_ACTIONS.get(agent, stage_label), status),
                    detail=f"标的 {input_params.ticker}",
                    agent=agent or None,
                )

            if event_type == "tool_call":
                tool_name = str(event_data.get("tool") or "unknown")
                _, _, fallback_agent = _tool_stage(tool_name)
                agent = event_data.get("agent") or fallback_agent
                status = str(event_data.get("status") or "running")
                action = tool_action(tool_name)
                call_id = str(event_data.get("activity_id") or _safe_step_id(tool_name))
                yield skill_progress(
                    stage_id=f"tool_{call_id}",
                    stage_label=action,
                    activity_id=call_id,
                    status=status if status in {"completed", "failed"} else "running",
                    step_id=f"tool_{call_id}",
                    step_label=activity_message(action, status),
                    detail=activity_detail(event_data.get("args")),
                    agent=agent,
                    data={
                        "tool": tool_name,
                        "args": event_data.get("args") if isinstance(event_data.get("args"), dict) else {},
                    },
                )

            # Save report to disk when complete
            if event_type == "report_complete":
                specialist_results = event_data.get("specialist_results", [])
                report_sections = event_data.get("sections", {})
                raw_decision = event_data.get("structured_portfolio_decision")
                if isinstance(raw_decision, dict):
                    structured_portfolio_decision = raw_decision
                yield skill_progress(
                    stage_id="report",
                    stage_label="生成完整报告",
                    status="running",
                    detail=f"汇总 {len(report_sections)} 个报告章节",
                    progress_pct=92,
                )
                report_path = self._save_report_to_disk(
                    ticker=input_params.ticker,
                    date=input_params.analysis_date,
                    sections=report_sections,
                    results_dir=run_config.get("results_dir", ""),
                )

            yield SkillEvent(event_type=event_type, data=event_data)

        # Record actual reuse, including a possible expiry while earlier roles
        # were running, rather than presenting the initial plan as execution.
        actual_reuse = {key: row["reused_from"] for row in specialist_results for key, (_, role, _) in REPORT_ROLES.items()
                        if row.get("role") == role and row.get("reused_from")}
        reused_labels = [REPORT_ROLES[key][2] for key in actual_reuse]
        fresh_labels = [label for key, (analyst, _, label) in REPORT_ROLES.items()
                        if analyst in input_params.analysts and key not in actual_reuse]

        # Save to database
        if report_path and report_sections:
            self._save_report_to_db(
                run_id=str(run_config.get("run_id", "")),
                ticker=input_params.ticker,
                sections=report_sections,
                report_path=report_path,
                db=run_config.get("db"),
            )

        # Build structured conclusion for frontend rendering
        structured_conclusion = self._extract_conclusion(
            report_sections,
            input_params.ticker,
            structured_decision=structured_portfolio_decision,
        )
        if input_params.analysis_template == "research":
            structured_conclusion.update(rating="Research", confidence=None, target_price=None,
                                         plan=None, decision_status="research_only",
                                         executive_summary="已完成所选研究角色的分析，本轮未执行交易决策与风控流程。")
        structured_conclusion = _apply_holding_execution_constraints(
            structured_conclusion,
            holding_context,
            market=market,
        )
        memory_usage = validate_memory_usage(
            (structured_portfolio_decision or {}).get("memory_usage") or [], selected_memory,
        )
        structured_conclusion["memory_trace"] = {
            "injected_ids": [row["id"] for row in selected_memory],
            "snapshots": selected_memory, "as_of_date": temporal_context.market_asof_date,
            "retrieved_ids": [row["id"] for row in selected_memory],
            "status": "model_reported" if memory_usage else "provided_to_analysis" if selected_memory else "not_injected",
            "usage": memory_usage, "warning": memory_error,
        }
        structured_conclusion["selection_alignment"] = reconcile_selection_analysis(
            input_params.selection_context,
            structured_conclusion,
            analysis_date=input_params.analysis_date,
        )

        # Persist structured conclusion + selection handoff as a Library artifact
        # so the plan is visible in Library (and C-2 can later adopt it into a
        # monitored Plan object).
        artifact_id = self._save_analysis_artifact(
            config,
            ticker=input_params.ticker,
            analysis_date=input_params.analysis_date,
            structured_conclusion=structured_conclusion,
            selection_context=input_params.selection_context,
            report_sections=report_sections,
            report_path=report_path,
        )
        # Enroll as a reflection case so the analysis feeds the reflection loop
        # (was missing — previously only daily_pipeline enrolled candidates).
        self._save_reflection_case(
            config,
            ticker=input_params.ticker,
            analysis_date=input_params.analysis_date,
            rating=str(structured_conclusion.get("rating") or ""),
            structured_conclusion=structured_conclusion,
            selection_context=input_params.selection_context,
            artifact_id=artifact_id,
        )

        yield skill_progress(
            stage_id="report",
            stage_label="生成完整报告",
            status="completed",
            detail="报告已保存并生成结构化结论",
            progress_pct=100,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "report_path": report_path,
                "ticker": input_params.ticker,
                "artifact_id": artifact_id,
                "as_of_date": temporal_context.market_asof_date,
                "analysis_template": input_params.analysis_template,
                "holding_context": holding_context,
                "selection_context": input_params.selection_context,
                "structured_conclusion": structured_conclusion,
                "specialist_results": specialist_results,
                "research_reuse": {"reused": reused_labels, "fresh": fresh_labels,
                                   "sources": list(actual_reuse.values())},
            },
        )

    def _save_report_to_disk(
        self,
        ticker: str,
        date: str,
        sections: dict[str, str],
        results_dir: str,
    ) -> str | None:
        """Save report sections to disk as markdown files."""
        from pathlib import Path

        from tradingagents.dataflows.utils import safe_ticker_component
        from tradingagents.reporting import write_report_sections

        if not results_dir or not sections:
            return None

        try:
            safe_ticker = safe_ticker_component(ticker)
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            report_dir = Path(results_dir) / f"{safe_ticker}_{timestamp}" / "reports"
            write_report_sections(sections, f"{ticker} ({date})", report_dir)
            return str(report_dir)
        except Exception as exc:
            logger.warning("Failed to save report to disk for %s: %s", ticker, exc)
            return None

    def _save_report_to_db(
        self,
        run_id: str,
        ticker: str,
        sections: dict[str, str],
        report_path: str,
        db: Any | None = None,
    ) -> None:
        """Save report to SQLite database for indexing."""
        import uuid

        from tradingagents.agents.utils.rating import parse_rating
        from tradingagents.core.persistence import Database

        if not sections:
            return

        try:
            if db is None:
                db = Database()
            rating = parse_rating(sections.get("final_trade_decision", ""))
            content = "\n\n".join(
                f"## {key.replace('_', ' ').title()}\n\n{val}"
                for key, val in sections.items()
            )
            db.save_report(
                report_id=str(uuid.uuid4()),
                run_id=run_id,
                ticker=ticker,
                rating=rating,
                content=content,
                path=report_path,
            )
        except Exception as exc:
            logger.warning("Failed to save stock analysis report to DB for %s: %s", ticker, exc)

    async def cancel(self) -> None:
        """Cancel is handled via asyncio.Task cancellation in RunManager."""
        pass

    def _save_analysis_artifact(
        self,
        config: dict[str, Any],
        *,
        ticker: str,
        analysis_date: str,
        structured_conclusion: dict[str, Any],
        selection_context: dict[str, Any] | None,
        report_sections: dict[str, str],
        report_path: str | None,
    ) -> str | None:
        """Persist the structured analysis (plan + selection handoff) to Library.

        Returns the artifact id so the caller can link a reflection case to it.
        """
        from tradingagents.core.artifacts import save_skill_artifact

        rating = structured_conclusion.get("rating") or ""
        content_markdown = (
            "\n\n".join(f"## {key.replace('_', ' ').title()}\n\n{val}" for key, val in report_sections.items())
            if report_sections
            else ""
        )
        return save_skill_artifact(
            config,
            skill_id="stock_analysis",
            artifact_type="stock_report",
            title=f"{ticker} 个股分析",
            subtitle=f"{rating} · {analysis_date}".strip(" ·"),
            subject_type="ticker",
            subject_id=ticker,
            subject_name=ticker,
            summary=rating,
            content_markdown=content_markdown,
            payload={
                "ticker": ticker,
                "analysis_date": analysis_date,
                "structured_conclusion": structured_conclusion,
                "selection_context": selection_context,
                "report_path": report_path,
            },
            tags=["stock_analysis", ticker],
        )

    def _save_reflection_case(
        self,
        config: dict[str, Any],
        *,
        ticker: str,
        analysis_date: str,
        rating: str,
        structured_conclusion: dict[str, Any],
        selection_context: dict[str, Any] | None,
        artifact_id: str | None,
    ) -> None:
        """Enroll this analysis as a reflection case (mirrors daily_pipeline).

        BUY/Overweight/Sell/Underweight → decision_grade + eligible for strategy
        learning; Hold/other → candidate_pool. Deterministic case_id so re-running
        on the same (ticker, date) replaces rather than duplicates.
        """
        db = config.get("db")
        if db is None:
            return
        try:
            enroll_reflection_case(
                db,
                source_type="stock_analysis",
                symbol=ticker,
                name=ticker,
                signal_date=analysis_date,
                rating_or_decision=rating,
                source_run_id=str(config.get("run_id", "")),
                source_artifact_id=str(artifact_id or ""),
                snapshot_payload={
                    "rating": rating,
                    "plan": structured_conclusion.get("plan"),
                    "target_price": structured_conclusion.get("target_price"),
                    "confidence": structured_conclusion.get("confidence"),
                    "reasons": structured_conclusion.get("reasons"),
                    "selection_context": selection_context,
                    "memory_trace": structured_conclusion.get("memory_trace"),
                },
                # stock_analysis scope: Buy/Overweight/Sell/Underweight ->
                # decision_grade; Hold/other -> candidate_pool.
                decision_grade_values=("buy", "overweight", "sell", "underweight"),
                candidate_pool_values=(),
                default_scope="candidate_pool",
            )
        except Exception as exc:
            logger.warning("Failed to enroll stock_analysis reflection case for %s: %s", ticker, exc)

    def _extract_conclusion(
        self,
        sections: dict[str, str],
        ticker: str,
        *,
        structured_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract structured conclusion JSON from report sections.

        Returns dict with keys: rating, target_price, confidence, reasons, symbol.
        """
        import re

        from tradingagents.agents.utils.rating import parse_rating

        decision_text = sections.get("final_trade_decision", "")

        # Primary path: the Portfolio Manager already returned a validated
        # PortfolioDecision. Carry it through directly; markdown remains a
        # human-readable report, not a machine-to-machine transport format.
        if structured_decision:
            try:
                from tradingagents.agents.schemas import PortfolioDecision

                decision = PortfolioDecision.model_validate(structured_decision)
                plan = (
                    decision.trade_plan.model_dump(mode="json")
                    if decision.trade_plan is not None
                    else None
                )
                reasons = [
                    value.strip()
                    for value in (decision.executive_summary, decision.investment_thesis)
                    if value and value.strip()
                ]
                base = {
                    "rating": decision.rating.value,
                    "target_price": decision.price_target,
                    "confidence": decision.confidence,
                    "reasons": reasons,
                    "executive_summary": decision.executive_summary,
                    "investment_thesis": decision.investment_thesis,
                    "time_horizon": decision.time_horizon,
                    "plan": plan,
                    "selection_reconciliation": (
                        decision.selection_reconciliation.model_dump(mode="json")
                        if decision.selection_reconciliation is not None
                        else None
                    ),
                }
                return _attach_instrument_identity(base, ticker)
            except Exception as exc:
                logger.warning(
                    "Invalid structured PortfolioDecision for %s; using markdown fallback: %s",
                    ticker,
                    exc,
                )

        rating = parse_rating(decision_text) if decision_text else "Hold"

        # Try extracting target price
        target_match = re.search(
            r"(?:\*\*)?(?:price\s+target|target|目标价)(?:\*\*)?[：:\s]*([¥$]?[\d,.]+)",
            decision_text,
            re.IGNORECASE,
        )
        target_price = _parse_float(target_match.group(1)) if target_match else None

        # Try extracting confidence
        conf_match = re.search(
            r"(?:\*\*)?(?:confidence|信心|把握)(?:\*\*)?[：:\s]*(\d+)",
            decision_text,
            re.IGNORECASE,
        )
        confidence = int(conf_match.group(1)) if conf_match else None

        # Extract numbered reason lines
        reason_matches = re.findall(r"^\d+[.、]\s*(.+)$", decision_text, re.MULTILINE)
        reasons = reason_matches[:5] if reason_matches else []
        if not reasons:
            reasons = [
                value
                for value in (
                    _extract_markdown_field(decision_text, "Executive Summary"),
                    _extract_markdown_field(decision_text, "Investment Thesis"),
                )
                if value
            ]

        # Structured trade plan (entry/stop/targets/position/conditions) — clean
        # round-trip when the PM used structured output; None on free-text fallback.
        plan = _extract_trade_plan(decision_text)

        return _attach_instrument_identity({
            "rating": rating,
            "target_price": target_price,
            "confidence": confidence,
            "reasons": reasons,
            "plan": plan,
            "executive_summary": _extract_markdown_field(decision_text, "Executive Summary"),
            "investment_thesis": _extract_markdown_field(decision_text, "Investment Thesis"),
            "time_horizon": _extract_markdown_field(decision_text, "Time Horizon"),
            "selection_reconciliation": _extract_selection_reconciliation(decision_text),
        }, ticker)


def _tool_stage(tool_name: str) -> tuple[str, str, str | None]:
    normalized = tool_name.lower()
    if any(token in normalized for token in ("price", "stock_data", "indicator", "market", "daily", "ohlcv", "theme_heat", "snapshot")):
        return "analyst_market", "市场分析", "Market Analyst"
    if any(token in normalized for token in ("sentiment", "social", "reddit", "stocktwits")):
        return "analyst_sentiment", "情绪分析", "Sentiment Analyst"
    if any(token in normalized for token in ("news", "announcement", "risk_announcement")):
        return "analyst_news", "新闻与公告分析", "News Analyst"
    if any(token in normalized for token in ("fundamental", "balance", "cashflow", "income", "financial", "metrics")):
        return "analyst_fundamentals", "基本面分析", "Fundamentals Analyst"
    if any(token in normalized for token in ("northbound", "flow", "institutional")):
        return "analyst_market", "资金流分析", "Market Analyst"
    return "agent_tool", "数据工具", None


def _safe_step_id(value: str) -> str:
    import re

    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    return normalized.strip("_") or "unknown"


async def _load_holding_context(
    db: Any | None,
    ticker: str,
    config: dict[str, Any],
    *,
    holding: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if db is None:
        return None
    try:
        from tradingagents.core.portfolio_prices import (
            latest_close,
            resolve_portfolio_name,
            resolve_portfolio_symbol,
        )

        symbol = resolve_portfolio_symbol(ticker)
        # When the caller (e.g. the portfolio UI) hands off raw holding fields,
        # skip the DB fetch and enrich that dict directly so the agent still
        # gets derived metrics (P&L, position weight) from a single source.
        if holding is None:
            holding = db.get_holding(symbol)
            if holding is None:
                return None
        elif holding.get("symbol"):
            symbol = str(holding["symbol"])

        holdings = db.list_holdings()
        current_price = optional_float(holding.get("current_price"))
        price_source = "portfolio_current_price" if current_price is not None else None
        price_trade_date = None
        if current_price is None and config.get("stock_analysis_refresh_holding_price_context", True):
            try:
                quote = await latest_close(symbol, config)
                if quote is not None:
                    current_price = optional_float(quote.get("close"))
                    price_source = str(quote.get("source") or "latest_close")
                    price_trade_date = quote.get("trade_date")
            except Exception as exc:
                logger.info("Holding context price refresh failed for %s: %s", symbol, exc)

        quantity = optional_float(holding.get("quantity")) or 0.0
        avg_cost = optional_float(holding.get("avg_cost")) or 0.0
        cost_value = quantity * avg_cost
        market_value = quantity * current_price if current_price is not None else None
        unrealized_pnl = market_value - cost_value if market_value is not None else None
        unrealized_return = unrealized_pnl / cost_value if unrealized_pnl is not None and cost_value else None

        total_market_value = 0.0
        for item in holdings:
            item_quantity = optional_float(item.get("quantity")) or 0.0
            item_price = optional_float(item.get("current_price")) or optional_float(item.get("avg_cost")) or 0.0
            total_market_value += item_quantity * item_price
        position_weight = market_value / total_market_value if market_value is not None and total_market_value else None

        return {
            "symbol": symbol,
            "name": holding.get("name") or resolve_portfolio_name(symbol),
            "quantity": quantity,
            "avg_cost": avg_cost,
            "current_price": current_price,
            "price_source": price_source,
            "price_trade_date": price_trade_date,
            "cost_value": round(cost_value, 2),
            "market_value": round(market_value, 2) if market_value is not None else None,
            "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
            "unrealized_return": unrealized_return,
            "position_weight": position_weight,
            "portfolio_market_value": round(total_market_value, 2),
            "holding_count": len(holdings),
            "notes": holding.get("notes"),
            "updated_at": holding.get("updated_at"),
        }
    except Exception as exc:
        logger.warning("Failed to load holding context for %s: %s", ticker, exc)
        return None


def _format_holding_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    lines = [
        "User-provided/tracked portfolio holding context (not a verified live account):",
        (
            "- These are supplied or locally tracked holdings. Do not assume they still match the user's actual account."
        ),
        f"- symbol/name: {context.get('symbol')} {context.get('name') or ''}".strip(),
        f"- quantity: {_format_number(context.get('quantity'))}",
        f"- average cost: {_format_number(context.get('avg_cost'))}",
    ]
    current_price = context.get("current_price")
    if current_price is not None:
        price_suffix = ""
        if context.get("price_trade_date"):
            price_suffix = f" as of {context.get('price_trade_date')}"
        lines.append(f"- reference price (unverified account cache): {_format_number(current_price)}{price_suffix}")
        lines.append("- This reference price and derived P&L/weights are not current market evidence. "
                     "Use this run's verified market snapshot for prices and price dates; "
                     "never override it with this cached reference or describe a mismatch as a price rebound.")
    if context.get("market_value") is not None:
        lines.append(f"- market value: {_format_number(context.get('market_value'))}")
    if context.get("unrealized_pnl") is not None:
        lines.append(
            "- unrealized P&L: "
            f"{_format_number(context.get('unrealized_pnl'))} "
            f"({_format_pct(context.get('unrealized_return'))})"
        )
    if context.get("position_weight") is not None:
        lines.append(f"- portfolio weight: {_format_pct(context.get('position_weight'))}")
    if context.get("portfolio_market_value") is not None:
        lines.append(
            f"- tracked portfolio market value: {_format_number(context.get('portfolio_market_value'))}; "
            f"holding count: {context.get('holding_count')}"
        )
    if context.get("notes"):
        lines.append(f"- user notes: {context.get('notes')}")
    lines.extend(
        [
            "- Required perspective: explicitly discuss add/hold/reduce/exit choices, "
            "cost-basis risk, position sizing, and whether the recommendation changes because the user already owns it.",
            "- If the standalone rating conflicts with portfolio risk management, surface that conflict clearly.",
        ]
    )
    quantity = optional_float(context.get("quantity")) or 0.0
    symbol = str(context.get("symbol") or "")
    if detect_market(symbol) == "cn_a" and quantity > 0:
        if quantity <= 100:
            lines.append(
                f"- HARD EXECUTION RULE: the current holding is {quantity:g} shares. "
                "A partial REDUCE is not executable; choose HOLD or EXIT the entire holding. "
                f"Never recommend selling or retaining a fraction of these {quantity:g} shares."
            )
        else:
            remainder = int(round(quantity)) % 100
            odd_lot_rule = (
                f" The {remainder}-share odd-lot remainder must be sold all at once."
                if remainder
                else ""
            )
            lines.append(
                "- HARD EXECUTION RULE: any partial A-share sell order must use 100-share lots;"
                f"{odd_lot_rule} Put exact executable order quantities in structured fields, "
                "not only in prose."
            )
    return "\n".join(lines)


def _apply_holding_execution_constraints(
    conclusion: dict[str, Any],
    holding_context: dict[str, Any] | None,
    *,
    market: str,
) -> dict[str, Any]:
    """Block LLM-generated holding adjustments that cannot be placed.

    Research ratings remain untouched, but a prose-only or invalid A-share
    REDUCE is converted to HOLD/review.  EXIT is unambiguous and normalized to
    the full current holding.  The original action/conditions remain in hidden
    audit metadata instead of being exposed as executable instructions.
    """

    if market != "cn_a" or not holding_context:
        return conclusion
    quantity_value = optional_float(holding_context.get("quantity"))
    if quantity_value is None or quantity_value <= 0:
        return conclusion
    quantity = int(round(quantity_value))
    if abs(quantity_value - quantity) > 1e-9:
        validation = {
            "status": "blocked",
            "market": "cn_a",
            "current_quantity": quantity_value,
            "lot_size": 100,
            "warnings": ["当前持仓不是整数股，无法生成可执行 A 股订单"],
        }
        conclusion["execution_validation"] = validation
        return conclusion

    plan = conclusion.get("plan")
    if not isinstance(plan, dict):
        return conclusion

    from tradingagents.core.order_constraints import (
        cn_a_partial_sell_available,
        validate_cn_a_sell_quantity,
    )

    original_plan_action = str(plan.get("plan_action") or "HOLD").upper()
    original_order_quantity = optional_float(plan.get("order_quantity"))
    original_conditions: list[dict[str, Any]] = []
    warnings: list[str] = []
    adjusted = False

    def block_condition(condition: dict[str, Any], reason: str) -> None:
        nonlocal adjusted
        original_conditions.append(dict(condition))
        condition["trigger_action"] = "HOLD"
        condition["order_quantity"] = None
        condition["target_quantity"] = quantity
        condition["description"] = (
            f"{reason}；该条件仅触发重新评估，不生成卖单。"
            f"如决定退出，必须一次性卖出全部 {quantity} 股。"
        )
        condition["source"] = "A股整手/零股确定性校验"
        adjusted = True

    conditions = plan.get("conditions")
    if not isinstance(conditions, list):
        conditions = []
        plan["conditions"] = conditions
    for raw_condition in conditions:
        if not isinstance(raw_condition, dict):
            continue
        action = str(raw_condition.get("trigger_action") or "").upper()
        order_quantity = optional_float(raw_condition.get("order_quantity"))
        if action == "REDUCE":
            if order_quantity is None:
                block_condition(raw_condition, "原减仓条件缺少结构化的精确卖出数量")
                continue
            valid, reason = validate_cn_a_sell_quantity(quantity, order_quantity)
            if not valid or order_quantity >= quantity:
                block_condition(raw_condition, reason or "REDUCE 不能等同于清仓")
                continue
            raw_condition["order_quantity"] = int(round(order_quantity))
            raw_condition["target_quantity"] = quantity - int(round(order_quantity))
        elif action == "EXIT":
            if order_quantity is not None:
                valid, _ = validate_cn_a_sell_quantity(quantity, order_quantity)
                if not valid or int(round(order_quantity)) != quantity:
                    original_conditions.append(dict(raw_condition))
                    adjusted = True
            raw_condition["order_quantity"] = quantity
            raw_condition["target_quantity"] = 0
            if adjusted and original_conditions and original_conditions[-1].get("description") == raw_condition.get("description"):
                raw_condition["description"] = (
                    f"满足该清仓条件时，一次性卖出当前全部 {quantity} 股；"
                    "不得拆分为零股卖单。"
                )
                raw_condition["source"] = "A股整手/零股确定性校验"
        elif action == "HOLD" and quantity <= 100:
            description = str(raw_condition.get("description") or "")
            share_values = [
                int(float(value))
                for value in re.findall(r"(\d+(?:\.\d+)?)\s*股", description)
            ]
            implies_fractional_remainder = bool(
                re.search(r"(?:保留|剩余|持仓).{0,16}(?:\d+(?:\.\d+)?\s*%|[一二三四五六七八九十]+分之)", description)
            )
            if any(value != quantity for value in share_values) or implies_fractional_remainder:
                block_condition(raw_condition, "原观察条件隐含了不可执行的零股剩余仓位")

    if original_plan_action == "REDUCE":
        if original_order_quantity is None:
            reason = "主计划缺少结构化的精确卖出数量"
            valid_primary = False
        else:
            valid_primary, reason = validate_cn_a_sell_quantity(
                quantity, original_order_quantity
            )
            valid_primary = valid_primary and original_order_quantity < quantity
            if not valid_primary and not reason:
                reason = "REDUCE 不能卖出全部持仓；清仓必须使用 EXIT"
        if not valid_primary:
            plan["plan_action"] = "HOLD"
            plan["order_quantity"] = None
            plan["target_quantity"] = quantity
            if holding_context.get("position_weight") is not None:
                plan["position_pct"] = round(
                    float(holding_context["position_weight"]) * 100, 2
                )
            warnings.append(
                f"{reason}；已将不可执行的 REDUCE 主计划降级为 HOLD/人工复核"
            )
            adjusted = True
        else:
            order_quantity = int(round(original_order_quantity))
            plan["order_quantity"] = order_quantity
            plan["target_quantity"] = quantity - order_quantity
    elif original_plan_action == "EXIT":
        plan["order_quantity"] = quantity
        plan["target_quantity"] = 0
    elif original_plan_action == "HOLD":
        plan["order_quantity"] = None
        plan["target_quantity"] = quantity

    if quantity <= 100 and original_plan_action == "REDUCE":
        warnings.append(
            f"当前仅持有 {quantity} 股，A 股不允许部分减仓；只能继续持有或一次性清仓"
        )

    validation = {
        "status": "adjusted" if adjusted else "valid",
        "market": "cn_a",
        "current_quantity": quantity,
        "lot_size": 100,
        "partial_sell_available": cn_a_partial_sell_available(quantity),
        "original_plan_action": original_plan_action,
        "executable_plan_action": str(plan.get("plan_action") or "HOLD").upper(),
        "order_quantity": plan.get("order_quantity"),
        "target_quantity": plan.get("target_quantity"),
        "warnings": warnings,
        "blocked_conditions": original_conditions,
    }
    plan["execution_validation"] = validation
    conclusion["execution_validation"] = validation
    return conclusion


def _format_selection_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    lines = ["Prior selection conclusion for this ticker (handoff from scanner/daily pipeline):"]
    decision = context.get("final_decision") or context.get("signal")
    if decision:
        lines.append(f"- selection final_decision: {decision}")
    score = context.get("display_score") or context.get("final_score") or context.get("quant_score")
    if score is not None:
        lines.append(f"- selection score: {score}")
    for key in (
        "quant_decision",
        "quant_score",
        "llm_view",
        "llm_score",
        "catalyst_strength",
        "risk_assessment",
        "score_confidence",
    ):
        value = context.get(key)
        if value is not None:
            lines.append(f"- selection {key}: {value}")
    for key in (
        "factor_scores",
        "data_coverage",
        "quant_gate_reasons",
        "gate_reasons",
        "risk_flags",
        "key_catalysts",
        "key_risks",
    ):
        value = context.get(key)
        if value:
            lines.append(f"- selection {key}: {value}")
    entry = context.get("entry_zone")
    if entry:
        lines.append(f"- selection entry_zone: {entry}")
    stop = context.get("stop_loss")
    if stop is not None:
        lines.append(f"- selection stop_loss: {stop}")
    targets = context.get("targets")
    if targets:
        lines.append(f"- selection targets: {targets}")
    action = context.get("action_plan")
    if isinstance(action, dict) and action:
        lines.append(f"- selection action_plan: {action}")
    reasoning = context.get("reasoning")
    if reasoning:
        lines.append(f"- selection LLM reasoning: {reasoning}")
    trade_date = context.get("trade_date") or context.get("price_trade_date")
    if trade_date:
        lines.append(f"- selection trade_date: {trade_date}")
    lines.extend([
        "- Required perspective: explicitly compare your analysis conclusion with the "
        "selection's plan above. If your rating/entry/stop conflicts with the selection, "
        "surface and explain the disagreement rather than silently diverging.",
    ])
    return "\n".join(lines)


def _format_reflection_context(reflections: list[dict[str, Any]]) -> str:
    if not reflections:
        return ""
    lines = ["Recent decision reflections from Library:"]
    for item in reflections[:5]:
        date_text = item.get("trade_date") or item.get("date") or "unknown date"
        decision = item.get("decision") or item.get("original_decision") or "unknown decision"
        actual_return = item.get("actual_return")
        was_correct = item.get("was_correct")
        reflection = item.get("reflection") or item.get("reflection_text") or ""
        outcome: list[str] = []
        if actual_return is not None:
            try:
                outcome.append(f"return {float(actual_return):+.2%}")
            except (TypeError, ValueError):
                outcome.append(f"return {actual_return}")
        if was_correct is not None:
            outcome.append("correct" if bool(was_correct) else "incorrect")
        suffix = f" ({', '.join(outcome)})" if outcome else ""
        lines.append(f"- {date_text}: {decision}{suffix}. {reflection}".strip())
    return "\n".join(lines)


def _join_context_blocks(*blocks: str) -> str:
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


def _attach_instrument_identity(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
    try:
        from tradingagents.core.portfolio_prices import resolve_portfolio_name

        name = resolve_portfolio_name(ticker)
    except Exception:
        name = ticker
    return {**payload, "symbol": ticker, "name": name}


def _extract_markdown_field(text: str, label: str) -> str | None:
    """Read a deterministic bold markdown field, including multiline prose."""
    match = re.search(
        rf"\*\*{re.escape(label)}\*\*\s*:\s*(.*?)(?=\n\s*\n\*\*|\Z)",
        text or "",
        re.IGNORECASE | re.DOTALL,
    )
    value = match.group(1).strip() if match else ""
    return value or None


def _extract_selection_reconciliation(text: str) -> dict[str, Any] | None:
    if "**Selection Reconciliation**" not in (text or ""):
        return None
    section = text.split("**Selection Reconciliation**", 1)[1]

    def line_value(label: str) -> str | None:
        match = re.search(rf"^- {re.escape(label)}:\s*(.+)$", section, re.MULTILINE)
        return match.group(1).strip() if match else None

    evidence = re.findall(r"^- New Evidence:\s*(.+)$", section, re.MULTILINE)
    changed = line_value("Decision Changed")
    result = {
        "prior_decision": line_value("Prior Decision"),
        "alignment": line_value("Alignment"),
        "decision_changed": str(changed or "").lower() in {"true", "yes", "1"},
        "explanation": line_value("Explanation"),
        "new_evidence": [item.strip() for item in evidence if item.strip()],
    }
    return result if any(value for value in result.values()) else None


def _format_number(value: Any) -> str:
    numeric = optional_float(value)
    if numeric is None:
        return "unknown"
    return f"{numeric:,.2f}"


def _format_pct(value: Any) -> str:
    numeric = optional_float(value)
    if numeric is None:
        return "unknown"
    return f"{numeric:+.2%}"


def _extract_trade_plan(decision_text: str) -> dict[str, Any] | None:
    """Parse the ``**Trade Plan**`` section produced by ``render_pm_decision``.

    Clean round-trip when the Portfolio Manager used structured output (the
    render format is deterministic). Returns None when there is no Trade Plan
    section (e.g. free-text fallback), so the frontend degrades gracefully.
    """
    if not decision_text or "**Trade Plan**" not in decision_text:
        return None
    plan: dict[str, Any] = {}

    action_match = re.search(r"- Action:\s*(ENTER|ADD|HOLD|REDUCE|EXIT)", decision_text, re.IGNORECASE)
    if action_match:
        plan["plan_action"] = action_match.group(1).upper()

    entry_match = re.search(
        r"- (?:Action|Entry) Zone:\s*\[([^\]]+)\]",
        decision_text,
        re.IGNORECASE,
    )
    if entry_match:
        vals = _parse_float_list(entry_match.group(1))
        if vals:
            plan["action_zone"] = vals
            plan["entry_zone"] = vals

    stop_match = re.search(
        r"- (?:Invalidation Level|Stop Loss):\s*([¥$]?[\d,.]+)",
        decision_text,
        re.IGNORECASE,
    )
    if stop_match:
        value = _parse_float(stop_match.group(1))
        plan["invalidation_level"] = value
        plan["stop_loss"] = value

    targets_match = re.search(
        r"- (?:Objective Levels|Targets):\s*\[([^\]]+)\]",
        decision_text,
        re.IGNORECASE,
    )
    if targets_match:
        vals = _parse_float_list(targets_match.group(1))
        if vals:
            plan["objective_levels"] = vals
            plan["targets"] = vals

    pos_match = re.search(r"- Position Sizing:\s*([¥$]?[\d,.]+)\s*%", decision_text)
    if pos_match:
        plan["position_pct"] = _parse_float(pos_match.group(1))

    cond_matches = re.findall(
        r"- Condition \[(entry|full|stop|take_profit)\](?:\s*\(([^)]*)\))?\s*:\s*(.+)",
        decision_text,
    )
    conditions: list[dict[str, Any]] = []
    trigger_matches = re.findall(
        r"- Trigger Action \[(ENTER|ADD|HOLD|REDUCE|EXIT)\]:\s*(.+)",
        decision_text,
        re.IGNORECASE,
    )
    trigger_by_description = {
        desc.strip(): action.upper() for action, desc in trigger_matches
    }
    for kind, source, desc in cond_matches:
        description = desc.strip()
        cond: dict[str, Any] = {"kind": kind, "description": description}
        trigger_action = trigger_by_description.get(description)
        if trigger_action:
            cond["trigger_action"] = trigger_action
        if source and source.strip():
            cond["source"] = source.strip()
        conditions.append(cond)
    if conditions:
        plan["conditions"] = conditions

    return plan or None


def _parse_float(text: str) -> float | None:
    try:
        return float(re.sub(r"[¥$,]", "", str(text)))
    except (TypeError, ValueError):
        return None


def _parse_float_list(text: str) -> list[float]:
    out: list[float] = []
    for part in str(text).split(","):
        val = _parse_float(part)
        if val is not None:
            out.append(val)
    return out


# Module-level export for auto_discover
skill = StockAnalysisSkill()
