"""Trader: turns the Research Manager's investment plan into a concrete transaction proposal."""

from __future__ import annotations

import functools

from langchain_core.messages import AIMessage

from tradingagents.agents.schemas import TraderProposal, render_trader_proposal
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_investment_style_instruction,
    get_language_instruction,
    get_market_risk_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_trader(llm):
    structured_llm = bind_structured(llm, TraderProposal, "Trader")

    def trader_node(state, name):
        company_name = state["company_of_interest"]
        market = state.get("market")
        instrument_context = get_instrument_context_from_state(state)
        investment_plan = state["investment_plan"]

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a trading agent turning the Research Manager's investment plan into a "
                    "concrete, executable transaction proposal. Ground your proposal in the analyst "
                    "reports and research plan. Specify: (1) action (Buy/Sell/Hold); (2) entry "
                    "approach — for A-shares prefer 集合竞价 (opening call 9:20-9:25) or first 30-min "
                    "VWAP, and never assume you can buy at 涨停 if buy orders dominate the queue; "
                    "(3) stop-loss in ATR×2 / -8% terms; (4) position sizing respecting T+1 and "
                    "single-name limits; (5) time horizon consistent with the investment style."
                    + get_market_risk_instruction(market)
                    + get_investment_style_instruction(state.get("investment_style"))
                    + get_language_instruction(market)
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on a comprehensive analysis by a team of analysts, here is an investment "
                    f"plan tailored for {company_name}. {instrument_context} This plan incorporates "
                    f"insights from current technical market trends, macroeconomic indicators, and "
                    f"social media sentiment. Use this plan as a foundation for evaluating your next "
                    f"trading decision.\n\nProposed Investment Plan: {investment_plan}\n\n"
                    f"Leverage these insights to make an informed and strategic decision."
                ),
            },
        ]

        trader_plan = invoke_structured_or_freetext(
            structured_llm,
            llm,
            messages,
            render_trader_proposal,
            "Trader",
        )

        return {
            "messages": [AIMessage(content=trader_plan)],
            "trader_investment_plan": trader_plan,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
