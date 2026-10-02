# TradingAgents/graph/setup.py

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_msg_delete,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.core.agent_runtime import role_node, runtime_model
from tradingagents.core.agent_specs import AGENT_SPECS, ANALYSIS_TEMPLATES

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic

    def setup_graph(
        self, selected_analysts=("market", "social", "news", "fundamentals"), template="full"
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        if template not in ANALYSIS_TEMPLATES:
            raise ValueError(f"Unknown analysis template: {template}")
        plan = build_analyst_execution_plan(selected_analysts)

        analyst_factories = {
            "market": lambda: create_market_analyst(runtime_model(self.quick_thinking_llm, "Market Analyst", purpose="default")),
            "social": lambda: create_sentiment_analyst(runtime_model(self.quick_thinking_llm, "Sentiment Analyst", purpose="default")),
            "news": lambda: create_news_analyst(runtime_model(self.quick_thinking_llm, "News Analyst", purpose="default")),
            "fundamentals": lambda: create_fundamentals_analyst(runtime_model(self.quick_thinking_llm, "Fundamentals Analyst", purpose="default")),
        }

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for spec in plan.specs:
            workflow.add_node(spec.agent_node, role_node(analyst_factories[spec.key](), AGENT_SPECS[spec.agent_node]))
            workflow.add_node(spec.clear_node, create_msg_delete())

        # Define edges
        # Start with the first analyst
        workflow.add_edge(START, plan.specs[0].agent_node)

        # Connect analysts in sequence
        for i, spec in enumerate(plan.specs):
            current_analyst = spec.agent_node
            current_clear = spec.clear_node

            # AgentSession executes each role's bounded tool loop internally.
            workflow.add_edge(current_analyst, current_clear)

            # Connect to next analyst or to Bull Researcher if this is the last analyst
            if i < len(plan.specs) - 1:
                workflow.add_edge(current_clear, plan.specs[i + 1].agent_node)
            else:
                workflow.add_edge(current_clear, END if template == "research" else "Bull Researcher")

        if template == "research":
            return workflow

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(runtime_model(self.quick_thinking_llm, "Bull Researcher", purpose="default"))
        bear_researcher_node = create_bear_researcher(runtime_model(self.quick_thinking_llm, "Bear Researcher", purpose="default"))
        research_manager_node = create_research_manager(runtime_model(self.deep_thinking_llm, "Research Manager", purpose="deep"))
        trader_node = create_trader(runtime_model(self.quick_thinking_llm, "Trader", purpose="default"))

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(runtime_model(self.quick_thinking_llm, "Aggressive Analyst", purpose="default"))
        neutral_analyst = create_neutral_debator(runtime_model(self.quick_thinking_llm, "Neutral Analyst", purpose="default"))
        conservative_analyst = create_conservative_debator(runtime_model(self.quick_thinking_llm, "Conservative Analyst", purpose="default"))
        portfolio_manager_node = create_portfolio_manager(runtime_model(self.deep_thinking_llm, "Portfolio Manager", purpose="deep"))

        # Add other nodes
        workflow.add_node("Bull Researcher", role_node(bull_researcher_node, AGENT_SPECS["Bull Researcher"]))
        workflow.add_node("Bear Researcher", role_node(bear_researcher_node, AGENT_SPECS["Bear Researcher"]))
        workflow.add_node("Research Manager", role_node(research_manager_node, AGENT_SPECS["Research Manager"]))
        workflow.add_node("Trader", role_node(trader_node, AGENT_SPECS["Trader"]))
        workflow.add_node("Aggressive Analyst", role_node(aggressive_analyst, AGENT_SPECS["Aggressive Analyst"]))
        workflow.add_node("Neutral Analyst", role_node(neutral_analyst, AGENT_SPECS["Neutral Analyst"]))
        workflow.add_node("Conservative Analyst", role_node(conservative_analyst, AGENT_SPECS["Conservative Analyst"]))
        workflow.add_node("Portfolio Manager", role_node(portfolio_manager_node, AGENT_SPECS["Portfolio Manager"]))

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
