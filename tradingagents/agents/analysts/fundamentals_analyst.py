from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_announcements,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_instrument_context_from_state,
    get_investment_style_instruction,
    get_language_instruction,
    get_lhb_detail,
    get_margin_balance,
    get_unlock_schedule,
)


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        market = state.get("market")
        instrument_context = get_instrument_context_from_state(state)
        style_instruction = get_investment_style_instruction(state.get("investment_style"))

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

        system_message = (
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

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " Your job is to produce a thorough analysis report, not a transaction proposal —"
                    " downstream agents (researcher, trader, portfolio manager) will decide the trade."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
