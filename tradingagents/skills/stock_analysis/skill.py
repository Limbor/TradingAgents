"""Stock analysis skill — wraps the existing TradingAgentsGraph.

This is the primary skill that exposes the multi-agent stock analysis
pipeline: 13 agents across 5 phases (Analyst → Research → Trader →
Risk → Portfolio Manager).
"""

from datetime import date, datetime as _dt
from typing import Any, AsyncIterator

import logging
import re
from pydantic import BaseModel, Field

from tradingagents.core.trading_time import get_temporal_context
from tradingagents.dataflows.symbol_utils import detect_market

logger = logging.getLogger(__name__)

from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress


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
    analysts: list[str] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"],
        description="Analysts to include in the pipeline",
    )
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
        default=True,
        description="When true, inject local holding context if the ticker is in the tracked portfolio.",
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

    rating: str = Field(description="Portfolio rating: Buy/Overweight/Hold/Underweight/Sell")
    executive_summary: str = Field(description="Executive summary of the decision")
    investment_thesis: str = Field(description="Detailed investment thesis")
    price_target: float | None = Field(default=None, description="Target price if set")
    time_horizon: str | None = Field(default=None, description="Recommended holding period")
    report_path: str | None = Field(default=None, description="Path to saved report")


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

    async def execute(
        self,
        params: BaseModel,
        config: dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        """Execute stock analysis, yielding events for real-time streaming."""
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        input_params: StockAnalysisInput = params
        raw_analysis_date = input_params.analysis_date
        market = detect_market(input_params.ticker)
        current_temporal_context = get_temporal_context(config, market=market)
        is_current_default = raw_analysis_date in {date.today().isoformat(), current_temporal_context.now[:10]}
        temporal_context = (
            current_temporal_context
            if is_current_default
            else get_temporal_context(config, market=market, requested_date=input_params.analysis_date)
        )
        if input_params.analysis_date != temporal_context.market_asof_date:
            input_params = input_params.model_copy(
                update={"analysis_date": temporal_context.market_asof_date}
            )

        db = config.get("db")
        holding_context = input_params.holding_context
        if holding_context is None and input_params.include_portfolio_context:
            holding_context = await _load_holding_context(
                db,
                input_params.ticker,
                config,
            )

        memory_context = _join_context_blocks(
            _format_holding_context(holding_context),
            _format_reflection_context(input_params.reflection_context),
            _format_selection_context(input_params.selection_context),
        )

        run_config = {
            **config,
            "max_debate_rounds": input_params.debate_rounds,
            "max_risk_discuss_rounds": input_params.risk_rounds,
            "memory_extra_context": memory_context,
            "holding_context": holding_context,
            "temporal_context": temporal_context.to_dict(),
            "market_asof_date": temporal_context.market_asof_date,
            "info_cutoff": temporal_context.info_cutoff,
        }

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

        ta = TradingAgentsGraph(
            selected_analysts=input_params.analysts,
            config=run_config,
        )

        report_path = None
        report_sections: dict[str, str] = {}

        async for event in ta.astream_propagate(
            ticker=input_params.ticker,
            date=input_params.analysis_date,
            selected_analysts=input_params.analysts,
            asset_type=input_params.asset_type,
        ):
            event_type = event["type"]
            event_data = event["data"]
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
                    status="completed" if status == "completed" else "running",
                    step_id=agent.lower().replace(" ", "_") if agent else None,
                    step_label=_agent_step_label(agent, status),
                    agent=agent or None,
                )

            if event_type == "tool_call":
                tool_name = str(event_data.get("tool") or "unknown")
                stage_id, stage_label, agent = _tool_stage(tool_name)
                yield skill_progress(
                    stage_id=stage_id,
                    stage_label=stage_label,
                    status="running",
                    step_id=f"tool_{_safe_step_id(tool_name)}",
                    step_label=f"调用数据工具：{_tool_label(tool_name)}",
                    detail=_tool_detail(tool_name, event_data.get("args")),
                    agent=agent,
                    data={
                        "tool": tool_name,
                        "args": event_data.get("args") if isinstance(event_data.get("args"), dict) else {},
                    },
                )

            # Save report to disk when complete
            if event_type == "report_complete":
                report_sections = event_data.get("sections", {})
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
        structured_conclusion = self._extract_conclusion(report_sections, input_params.ticker)

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
                "holding_context": holding_context,
                "selection_context": input_params.selection_context,
                "structured_conclusion": structured_conclusion,
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
            artifact_type="analysis_report",
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
            rating_upper = str(rating or "").lower()
            if rating_upper in {"buy", "overweight", "sell", "underweight"}:
                scope = "decision_grade"
                eligible = True
            else:
                scope = "candidate_pool"
                eligible = False
            case_id = f"stock_analysis:{analysis_date}:{ticker.upper()}"
            db.save_reflection_case(
                case_id=case_id,
                source_type="stock_analysis",
                reflection_scope=scope,
                eligible_for_strategy_learning=eligible,
                symbol=ticker,
                signal_date=analysis_date,
                horizon_days=5,
                source_run_id=str(config.get("run_id", "")),
                source_artifact_id=str(artifact_id or ""),
                name=ticker,
                snapshot_payload={
                    "rating": rating,
                    "plan": structured_conclusion.get("plan"),
                    "target_price": structured_conclusion.get("target_price"),
                    "confidence": structured_conclusion.get("confidence"),
                    "reasons": structured_conclusion.get("reasons"),
                    "selection_context": selection_context,
                },
                status="pending",
            )
        except Exception as exc:
            logger.warning("Failed to enroll stock_analysis reflection case for %s: %s", ticker, exc)

    def _extract_conclusion(
        self, sections: dict[str, str], ticker: str
    ) -> dict[str, Any]:
        """Extract structured conclusion JSON from report sections.

        Returns dict with keys: rating, target_price, confidence, reasons, symbol.
        """
        import re
        from tradingagents.agents.utils.rating import parse_rating

        decision_text = sections.get("final_trade_decision", "")
        rating = parse_rating(decision_text) if decision_text else "Hold"

        # Try extracting target price
        target_match = re.search(
            r"(?:target|目标价)[：:\s]*([¥$]?[\d,.]+)", decision_text, re.IGNORECASE
        )
        target_price = target_match.group(1) if target_match else None

        # Try extracting confidence
        conf_match = re.search(
            r"(?:confidence|信心|把握)[：:\s]*(\d+)", decision_text, re.IGNORECASE
        )
        confidence = int(conf_match.group(1)) if conf_match else None

        # Extract numbered reason lines
        reason_matches = re.findall(r"^\d+[.、]\s*(.+)$", decision_text, re.MULTILINE)
        reasons = reason_matches[:5] if reason_matches else []

        # Structured trade plan (entry/stop/targets/position/conditions) — clean
        # round-trip when the PM used structured output; None on free-text fallback.
        plan = _extract_trade_plan(decision_text)

        return {
            "rating": rating,
            "target_price": target_price,
            "confidence": confidence,
            "reasons": reasons,
            "plan": plan,
            "symbol": ticker,
        }


def _agent_step_label(agent: str, status: str) -> str:
    stage = AGENT_PROGRESS_STAGES.get(agent)
    label = stage[1] if stage else agent or "Agent"
    if status == "completed":
        return f"{label}完成"
    if status == "failed":
        return f"{label}失败"
    return f"{label}进行中"


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


def _tool_label(tool_name: str) -> str:
    normalized = tool_name.lower()
    labels = {
        "get_stock_data": "行情数据",
        "get_verified_market_snapshot": "市场快照",
        "get_market_structure_snapshot": "市场结构",
        "get_indicators": "技术指标",
        "get_theme_heat": "主题热度",
        "get_news": "新闻",
        "get_announcements": "公告",
        "get_risk_announcements": "风险公告",
        "get_fundamentals": "基本面",
        "get_balance_sheet": "资产负债表",
        "get_cashflow": "现金流",
        "get_income_statement": "利润表",
        "get_northbound_flow": "北向资金",
        "get_institutional_flow": "机构资金流",
    }
    for key, label in labels.items():
        if key in normalized:
            return label
    return tool_name


def _tool_detail(tool_name: str, args: Any) -> str:
    if not isinstance(args, dict) or not args:
        return tool_name
    interesting = [
        "ticker",
        "symbol",
        "ts_code",
        "curr_date",
        "date",
        "start_date",
        "end_date",
        "look_back_days",
        "indicator",
        "top_n",
    ]
    parts = [f"{key}={args[key]}" for key in interesting if key in args]
    if not parts:
        parts = [f"{key}={value}" for key, value in list(args.items())[:4]]
    detail = ", ".join(str(part) for part in parts)
    return detail[:220]


def _safe_step_id(value: str) -> str:
    import re

    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    return normalized.strip("_") or "unknown"


async def _load_holding_context(
    db: Any | None,
    ticker: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    if db is None:
        return None
    try:
        from tradingagents.core.portfolio_prices import latest_close, resolve_portfolio_name, resolve_portfolio_symbol

        symbol = resolve_portfolio_symbol(ticker)
        holding = db.get_holding(symbol)
        if holding is None:
            return None

        holdings = db.list_holdings()
        current_price = _optional_float(holding.get("current_price"))
        price_source = "portfolio_current_price" if current_price is not None else None
        price_trade_date = None
        if current_price is None and config.get("stock_analysis_refresh_holding_price_context", True):
            try:
                quote = await latest_close(symbol, config)
                if quote is not None:
                    current_price = _optional_float(quote.get("close"))
                    price_source = str(quote.get("source") or "latest_close")
                    price_trade_date = quote.get("trade_date")
            except Exception as exc:
                logger.info("Holding context price refresh failed for %s: %s", symbol, exc)

        quantity = _optional_float(holding.get("quantity")) or 0.0
        avg_cost = _optional_float(holding.get("avg_cost")) or 0.0
        cost_value = quantity * avg_cost
        market_value = quantity * current_price if current_price is not None else None
        unrealized_pnl = market_value - cost_value if market_value is not None else None
        unrealized_return = unrealized_pnl / cost_value if unrealized_pnl is not None and cost_value else None

        total_market_value = 0.0
        for item in holdings:
            item_quantity = _optional_float(item.get("quantity")) or 0.0
            item_price = _optional_float(item.get("current_price")) or _optional_float(item.get("avg_cost")) or 0.0
            total_market_value += item_quantity * item_price
        position_weight = market_value / total_market_value if market_value is not None and total_market_value else None

        return {
            "symbol": symbol,
            "name": resolve_portfolio_name(symbol),
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
        "User portfolio holding context:",
        (
            f"- This ticker is already held by the user. Analyze it as a position review, "
            f"not only as a standalone stock pitch."
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
        lines.append(f"- latest/current price: {_format_number(current_price)}{price_suffix}")
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
    return "\n".join(lines)


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


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: Any) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return "unknown"
    return f"{numeric:,.2f}"


def _format_pct(value: Any) -> str:
    numeric = _optional_float(value)
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

    entry_match = re.search(r"- Entry Zone:\s*\[([^\]]+)\]", decision_text)
    if entry_match:
        vals = _parse_float_list(entry_match.group(1))
        if vals:
            plan["entry_zone"] = vals

    stop_match = re.search(r"- Stop Loss:\s*([¥$]?[\d,.]+)", decision_text)
    if stop_match:
        plan["stop_loss"] = _parse_float(stop_match.group(1))

    targets_match = re.search(r"- Targets:\s*\[([^\]]+)\]", decision_text)
    if targets_match:
        vals = _parse_float_list(targets_match.group(1))
        if vals:
            plan["targets"] = vals

    pos_match = re.search(r"- Position Sizing:\s*([¥$]?[\d,.]+)\s*%", decision_text)
    if pos_match:
        plan["position_pct"] = _parse_float(pos_match.group(1))

    cond_matches = re.findall(
        r"- Condition \[(entry|full|stop|take_profit)\](?:\s*\(([^)]*)\))?\s*:\s*(.+)",
        decision_text,
    )
    conditions: list[dict[str, Any]] = []
    for kind, source, desc in cond_matches:
        cond: dict[str, Any] = {"kind": kind, "description": desc.strip()}
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
