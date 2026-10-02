from tradingagents.agents.utils.agent_utils import (
    get_global_news,
    get_investment_style_instruction,
    get_language_instruction,
    get_macro_calendar,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)
from tradingagents.agents.utils.create_tool_analyst import create_tool_analyst


def create_news_analyst(llm):
    def tools(state):
        market = state.get("market")
        # For A-shares, drop US-only tools (FRED macro indicators, Polymarket
        # prediction markets) — they are rarely material for A-share names and
        # can mislead the LLM into producing irrelevant US context.
        if market == "cn_a":
            return [get_news, get_global_news, get_macro_calendar]
        return [get_news, get_global_news, get_macro_indicators, get_macro_calendar, get_prediction_markets]

    def system_message(state):
        market = state.get("market")
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        style_instruction = get_investment_style_instruction(state.get("investment_style"))
        if market == "cn_a":
            return (
                f"You are a news researcher analyzing recent news and trends over the past week for an A-share {asset_label}. "
                "Lead with 政策面 (policy)、监管动态 (regulation)、行业政策 (industry policy), and ground macro commentary "
                "in get_macro_calendar (CPI/PPI/PMI/M2/LPR/SHIBOR). Use get_news for {asset_label}-specific or targeted searches, "
                "get_global_news for broader macro context. Down-weight US macro (FRED) and prediction markets — they are rarely "
                "material for A-share names. Cover: 业绩预告/快报、重组/增减持/回购、问询函/监管函、限售解禁、北向资金动向、"
                "板块轮动. Provide specific, actionable insights with supporting evidence."
                + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
                + get_language_instruction(market)
                + style_instruction
            )
        return (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for {asset_label}-specific or targeted news searches, get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, curr_date, look_back_days) to ground US macro commentary in FRED data, get_macro_calendar(curr_date, look_back_days) to ground China A-share macro commentary in CPI/PPI/PMI/M2/LPR/SHIBOR data, and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction(market)
            + style_instruction
        )

    return create_tool_analyst(llm, tools, system_message, "news_report")
