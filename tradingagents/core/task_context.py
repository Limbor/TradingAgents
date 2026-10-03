"""Small durable research state. Preferences are neither evidence nor authority."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class ResearchTaskContext(BaseModel):
    version: int = 1
    target: str = "general"
    symbols: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    horizon: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    request: str = ""
    inherited_from: str | None = None
    last_skill: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


def is_continuation(goal: str) -> bool:
    """Conservative fallback; model routing receives explicit state and user text."""
    return bool(re.search(r"这只|该股|这家公司|这个标的|它|继续|重新推荐|再推荐|换一个|"
                          r"怎么只有|有没有基本面|从营收|从行业|中线的话|长期的话|那(?:么|个股)", goal))


def build_task_context(goal: str, previous: dict | None = None, *, previous_task_id=None,
                       paper_session_id=None, symbols=()) -> dict:
    context = ResearchTaskContext()
    if previous and is_continuation(goal):
        context = ResearchTaskContext.model_validate(previous)
        context.inherited_from = previous_task_id
        context.evidence_refs = []  # Old facts are never current evidence.
    context.request = goal[:1000]
    if paper_session_id:
        context.target = "paper"
    elif any(word in goal for word in ("个股", "选股", "股票", "该股", "这只")) or symbols:
        context.target = "stock"
    elif any(word in goal for word in ("板块", "行业")) and not is_continuation(goal):
        context.target = "sector"
    if symbols:
        context.symbols = list(symbols)[:2]
    if any(word in goal for word in ("排除双创", "排除科创", "排除创业", "只看主板", "主板股")):
        context.filters["board_filter"] = "main_board"
    elif any(word in goal for word in ("只看双创", "仅双创")):
        context.filters["board_filter"] = "dual_growth_only"
    elif any(word in goal for word in ("不限板", "包含双创", "取消板型限制")):
        context.filters.pop("board_filter", None)
    if "一个" in goal or "一只" in goal:
        context.filters["limit"] = 1
    for phrase, horizon in (("短线", "short_term"), ("中线", "medium_term"), ("长期", "long_term"), ("长线", "long_term")):
        if phrase in goal:
            context.horizon = horizon
    dimensions = [name for name, words in {
        "market": ("技术", "走势", "股价"), "fundamentals": ("基本面", "营收", "利润", "现金流"),
        "news": ("新闻", "公告"), "social": ("情绪",),
    }.items() if any(word in goal for word in words)]
    if dimensions:
        context.dimensions = dimensions
    return context.model_dump()


def apply_context_args(skill_id: str, args: dict, context: dict) -> dict:
    """Only apply persistent filters where the executor supports them exactly."""
    result = dict(args)
    if skill_id == "daily_pipeline":
        for key in ("board_filter", "limit"):
            if key in context.get("filters", {}):
                result[key] = context["filters"][key]
        if context.get("industries"):
            result.setdefault("industries", context["industries"])
    return result


def accept_skill_context(context: dict, skill_id: str, args: dict) -> dict:
    """Only validated execution parameters may confirm a research object."""
    context = ResearchTaskContext.model_validate(context).model_dump()
    context["last_skill"] = skill_id
    if skill_id == "stock_analysis":
        context["target"] = "stock"
        ticker = str(args.get("ticker", "")).upper().replace(".SS", ".SH")
        if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", ticker):
            context["symbols"] = [ticker]
        context["dimensions"] = args.get("analysts", context["dimensions"])
    elif skill_id == "market_overview":
        context["target"] = "sector"
        context["symbols"] = []
        context["industries"] = args.get("focus_industries", [])
    elif skill_id in {"market_scanner", "daily_pipeline"}:
        context["target"] = "stock_selection"
        context["symbols"] = []
        context["industries"] = args.get("industries") or ([args["theme"]] if args.get("theme") else [])
        for key in ("board_filter", "limit"):
            if key in args:
                context["filters"][key] = args[key]
    return context
