"""Stock analysis skill — wraps the existing TradingAgentsGraph.

This is the primary skill that exposes the multi-agent stock analysis
pipeline: 13 agents across 5 phases (Analyst → Research → Trader →
Risk → Portfolio Manager).
"""

from datetime import date, datetime as _dt
from typing import Any, AsyncIterator

import logging
from pydantic import BaseModel, Field

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
        default=["market", "social", "news", "fundamentals"],
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

        run_config = {
            **config,
            "max_debate_rounds": input_params.debate_rounds,
            "max_risk_discuss_rounds": input_params.risk_rounds,
        }

        yield SkillEvent(
            event_type="skill_start",
            data={
                "skill_id": "stock_analysis",
                "ticker": input_params.ticker,
                "date": input_params.analysis_date,
            },
        )
        yield skill_progress(
            stage_id="prepare",
            stage_label="准备分析",
            status="completed",
            detail=f"{input_params.ticker} · {input_params.analysis_date}",
            progress_pct=5,
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

        if not results_dir or not sections:
            return None

        try:
            safe_ticker = safe_ticker_component(ticker)
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            report_dir = Path(results_dir) / f"{safe_ticker}_{timestamp}" / "reports"
            report_dir.mkdir(parents=True, exist_ok=True)

            # Save individual sections
            section_files = {
                "market_report": "1_market.md",
                "sentiment_report": "2_sentiment.md",
                "news_report": "3_news.md",
                "fundamentals_report": "4_fundamentals.md",
                "investment_plan": "5_research.md",
                "trader_investment_plan": "6_trader.md",
                "final_trade_decision": "7_portfolio.md",
            }
            for key, filename in section_files.items():
                if key in sections:
                    (report_dir / filename).write_text(sections[key], encoding="utf-8")

            # Save complete report
            complete = "\n\n".join(
                f"# {key.replace('_', ' ').title()}\n\n{content}"
                for key, content in sections.items()
            )
            complete_path = report_dir / "complete_report.md"
            complete_path.write_text(
                f"# Trading Analysis Report: {ticker} ({date})\n\n{complete}",
                encoding="utf-8",
            )

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

        return {
            "rating": rating,
            "target_price": target_price,
            "confidence": confidence,
            "reasons": reasons,
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


# Module-level export for auto_discover
skill = StockAnalysisSkill()
