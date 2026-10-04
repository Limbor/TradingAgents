"""Readable execution messages. Only describe work that actually starts."""

import logging
from typing import Any

AGENT_ACTIONS = {
    "Market Analyst": "分析价格走势与技术指标",
    "Sentiment Analyst": "分析市场情绪",
    "News Analyst": "核对新闻与公司公告",
    "Fundamentals Analyst": "分析财报与估值",
    "Bull Researcher": "评估上涨逻辑与催化剂",
    "Bear Researcher": "检查下行风险与反面证据",
    "Research Manager": "综合多空观点",
    "Trader": "制定交易计划",
    "Aggressive Analyst": "评估进攻方案的风险",
    "Conservative Analyst": "检查防守与回撤约束",
    "Neutral Analyst": "平衡收益与风险",
    "Portfolio Manager": "核对条件与风险并形成最终判断",
}

# Specific names precede generic substrings (e.g. risk announcements/news).
TOOL_ACTIONS = {
    "get_paper_session": "读取模拟盘账本与持仓",
    "get_paper_job": "查询模拟盘推进进度",
    "get_portfolio_summary": "读取持仓概览",
    "get_recent_runs": "查询近期任务记录",
    "search_artifacts": "查找已有研究报告",
    "get_strategy_lessons": "检索适用的历史经验",
    "get_mcp_factor_snapshot": "查询价格与量化因子",
    "risk_announcements": "核对风险公告",
    "announcements": "查询公司公告",
    "get_stock_data": "查询股票价格",
    "get_verified_market_snapshot": "核对市场行情快照",
    "get_market_structure_snapshot": "查询市场结构",
    "get_indicators": "计算技术指标",
    "get_theme_heat": "查询板块热度",
    "get_global_news": "查询市场新闻",
    "get_news": "查询相关新闻",
    "get_balance_sheet": "查询资产负债表",
    "get_cashflow": "查询现金流",
    "get_income_statement": "查询利润表",
    "get_fundamentals": "查询基本面与估值",
    "get_northbound_flow": "查询北向资金",
    "get_institutional_flow": "查询机构资金流",
}

SKILL_ACTIONS = {
    "stock_analysis": "分析股票走势、基本面与风险",
    "market_scanner": "扫描市场候选股票",
    "market_overview": "分析市场与板块机会",
    "daily_pipeline": "筛选并复核股票候选",
    "portfolio_management": "核对持仓与组合风险",
    "risk_monitor": "检查持仓风险",
    "daily_review": "复盘交易表现",
}


def tool_action(name: str) -> str:
    normalized = name.lower()
    return next(
        (label for key, label in TOOL_ACTIONS.items() if key in normalized), "查询分析所需数据"
    )


def activity_detail(args: Any) -> str | None:
    """Whitelist display fields; never render raw tool arguments or credentials."""
    if not isinstance(args, dict):
        return None
    parts = []
    for keys, label in (
        (("ticker", "symbol", "ts_code"), "标的"),
        (("curr_date", "date", "analysis_date", "trade_date"), "基准日"),
        (("start_date",), "起始日"),
        (("end_date",), "截止日"),
        (("indicator",), "指标"),
    ):
        value = next(
            (args[key] for key in keys if isinstance(args.get(key), (str, int, float))), None
        )
        if value is not None:
            parts.append(f"{label} {str(value)[:40]}")
    return " · ".join(parts) or None


def activity_message(action: str, status: str) -> str:
    if status == "completed":
        return f"{action}完成"
    if status == "failed":
        return f"{action}失败"
    return f"正在{action}"


async def report_activity(
    config: dict,
    activity_id: str,
    action: str,
    status: str,
    *,
    detail: str | None = None,
    agent: str | None = None,
) -> None:
    """Optional progress observer; its failure must not change a trading result."""
    callback = config.get("_activity_progress")
    if callback is None:
        return
    try:
        await callback(
            {
                "stage_id": activity_id,
                "activity_id": activity_id,
                "stage_label": action,
                "step_label": activity_message(action, status),
                "status": status,
                "detail": detail,
                "agent": agent,
            }
        )
    except Exception:
        logging.getLogger(__name__).debug("Activity observer unavailable", exc_info=True)
