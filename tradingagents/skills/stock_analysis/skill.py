"""Stock analysis skill — wraps the existing TradingAgentsGraph.

This is the primary skill that exposes the multi-agent stock analysis
pipeline: 13 agents across 5 phases (Analyst → Research → Trader →
Risk → Portfolio Manager).
"""

from datetime import date
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


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

            # Save report to disk when complete
            if event_type == "report_complete":
                report_sections = event_data.get("sections", {})
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

        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "report_path": report_path,
                "ticker": input_params.ticker,
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
        from tradingagents.agents.utils.rating import parse_rating

        if not results_dir or not sections:
            return None

        try:
            safe_ticker = safe_ticker_component(ticker)
            timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
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
        except Exception:
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
        except Exception:
            pass

    async def cancel(self) -> None:
        """Cancel is handled via asyncio.Task cancellation in RunManager."""
        pass


# Module-level export for auto_discover
skill = StockAnalysisSkill()
