"""Professional roles and reusable analysis templates."""
from tradingagents.core.agent_runtime import AgentSpec

AGENT_SPECS = {
    spec.name: spec for spec in (
        AgentSpec("Trading Coordinator"),
        AgentSpec("Task Planner"),
        AgentSpec("Evidence Writer"),
        AgentSpec("Market Analyst", report_key="market_report"),
        AgentSpec("Sentiment Analyst", report_key="sentiment_report"),
        AgentSpec("News Analyst", report_key="news_report"),
        AgentSpec("Fundamentals Analyst", report_key="fundamentals_report"),
        AgentSpec("Bull Researcher"), AgentSpec("Bear Researcher"),
        AgentSpec("Research Manager", purpose="deep", report_key="investment_plan"),
        AgentSpec("Trader", report_key="trader_investment_plan"),
        AgentSpec("Aggressive Analyst"), AgentSpec("Neutral Analyst"), AgentSpec("Conservative Analyst"),
        AgentSpec("Portfolio Manager", purpose="deep", report_key="final_trade_decision"),
        AgentSpec("Candidate Reviewer"), AgentSpec("Market Researcher"),
        AgentSpec("Reflection Agent"), AgentSpec("Pattern Researcher"),
    )
}

ANALYSIS_TEMPLATES = {
    "full": {"research": True, "risk": True},
    "research": {"research": False, "risk": False},
}
