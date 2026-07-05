"""Reusable markdown report writers for completed analysis runs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


SECTION_FILES = {
    "market_report": ("1_analysts", "market.md", "Market Analyst"),
    "sentiment_report": ("1_analysts", "sentiment.md", "Sentiment Analyst"),
    "news_report": ("1_analysts", "news.md", "News Analyst"),
    "fundamentals_report": ("1_analysts", "fundamentals.md", "Fundamentals Analyst"),
    "investment_plan": ("2_research", "manager.md", "Research Team Decision"),
    "trader_investment_plan": ("3_trading", "trader.md", "Trader"),
    "final_trade_decision": ("5_portfolio", "decision.md", "Portfolio Manager"),
}


def write_report_sections(sections: dict[str, str], ticker: str, save_path: str | Path) -> Path:
    """Write per-section markdown files and a consolidated complete report."""

    root = Path(save_path)
    root.mkdir(parents=True, exist_ok=True)
    complete_parts: list[str] = []

    for key, content in sections.items():
        if not content:
            continue
        dirname, filename, title = SECTION_FILES.get(
            key,
            ("sections", f"{_safe_filename(key)}.md", key.replace("_", " ").title()),
        )
        section_dir = root / dirname
        section_dir.mkdir(exist_ok=True)
        (section_dir / filename).write_text(content, encoding="utf-8")
        complete_parts.append(f"## {title}\n\n{content}")

    header = (
        f"# Trading Analysis Report: {ticker}\n\n"
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    complete_path = root / "complete_report.md"
    complete_path.write_text(header + "\n\n".join(complete_parts), encoding="utf-8")
    return complete_path


def write_report_tree(final_state: dict[str, Any], ticker: str, save_path: str | Path) -> Path:
    """Write a completed LangGraph state as a report tree.

    This mirrors the upstream v0.3.0 writer while staying compatible with this
    fork's section naming. Programmatic callers can use it when they have the
    final graph state rather than the flattened API sections.
    """

    sections: dict[str, str] = {}
    for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
        if final_state.get(key):
            sections[key] = str(final_state[key])

    debate = final_state.get("investment_debate_state")
    if isinstance(debate, dict):
        research_parts = []
        if debate.get("bull_history"):
            research_parts.append(f"### Bull Researcher\n{debate['bull_history']}")
        if debate.get("bear_history"):
            research_parts.append(f"### Bear Researcher\n{debate['bear_history']}")
        if debate.get("judge_decision"):
            research_parts.append(f"### Research Manager\n{debate['judge_decision']}")
        if research_parts:
            sections["investment_plan"] = "\n\n".join(research_parts)

    if final_state.get("trader_investment_plan"):
        sections["trader_investment_plan"] = str(final_state["trader_investment_plan"])

    risk = final_state.get("risk_debate_state")
    if isinstance(risk, dict):
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_parts.append(f"### Aggressive Analyst\n{risk['aggressive_history']}")
        if risk.get("conservative_history"):
            risk_parts.append(f"### Conservative Analyst\n{risk['conservative_history']}")
        if risk.get("neutral_history"):
            risk_parts.append(f"### Neutral Analyst\n{risk['neutral_history']}")
        if risk_parts:
            sections["risk_plan"] = "\n\n".join(risk_parts)
        if risk.get("judge_decision"):
            sections["final_trade_decision"] = str(risk["judge_decision"])

    return write_report_sections(sections, ticker, save_path)


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value).lower())
    return safe.strip("_") or "section"
