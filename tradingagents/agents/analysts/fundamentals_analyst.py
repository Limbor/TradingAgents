from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_announcements,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_investment_style_instruction,
    get_language_instruction,
    get_lhb_detail,
    get_margin_balance,
    get_unlock_schedule,
)
from tradingagents.agents.utils.create_tool_analyst import create_tool_analyst


def create_fundamentals_analyst(llm):
    tools = [
        get_fundamentals,
        get_balance_sheet,
        get_cashflow,
        get_income_statement,
        get_announcements,
        get_lhb_detail,
        get_margin_balance,
        get_unlock_schedule,
    ]

    def system_message(state):
        market = state.get("market")
        style_instruction = get_investment_style_instruction(state.get("investment_style"))
        return (
            "You are a researcher analyzing the fundamental profile of a company. Cover: "
            "(1) valuation — PE-TTM, PB, PS-TTM, EV/EBITDA if available, and the stock's "
            "valuation percentile within its industry over the past 3-5 years; "
            "(2) profitability — ROE, gross margin (毛利率), net margin, and their trends; "
            "(3) growth — revenue and net-profit YoY/QoQ; "
            "(4) balance sheet — leverage (资产负债率), cash position, operating cash flow; "
            "(5) capital events — dividends (分红), 送转, buybacks, restricted-share unlocks "
            "(限售解禁), insider (董监高) 增减持. "
            "For China A-shares, financials follow CN GAAP; watch 业绩快报/业绩预告 for early "
            "reads and annual/quarterly report disclosure windows. State explicitly whether "
            "price data is 前复权 (forward-adjusted) to avoid false signals from 除权除息."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis (call it first for the valuation percentile), `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + " For China A-shares, also use `get_announcements`, `get_lhb_detail`, `get_margin_balance`, and `get_unlock_schedule` to cover公告/监管、龙虎榜资金、融资融券和解禁风险."
            + get_language_instruction(market)
            + style_instruction,
        )

    return create_tool_analyst(llm, tools, system_message, "fundamentals_report")
