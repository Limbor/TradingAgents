"""Factory for tool-calling analyst nodes.

``market_analyst``, ``fundamentals_analyst``, and ``news_analyst`` share the
same shape: an identical ``ChatPromptTemplate``, the same
``prompt | llm.bind_tools`` chain assembly, the same result-extraction logic
(report = ``result.content`` when there are no tool calls, else ``""``), and
the same ``{"messages": [result], <key>: report}`` state update. Only
``system_message``, ``tools``, and ``report_key`` differ between them.

This module factors that shared structure into :func:`create_tool_analyst` so
the three analysts only carry what is unique to them. ``sentiment_analyst`` is
a deliberate exception (pre-fetched data injected into the prompt + structured
output, no tool-calling) and does not use this factory.

Behavior is preserved verbatim: the prompt template text, the ``partial``
calls, tool binding, invocation, result extraction, and state-update keys are
identical to the inlined originals.
"""

from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import get_instrument_context_from_state


def create_tool_analyst(
    llm,
    tools,
    system_message,
    report_key: str,
) -> Callable[[Mapping[str, Any]], dict]:
    """Build a tool-calling analyst node from the shared template.

    Parameters
    ----------
    llm:
        The language model to bind tools to (the same object the analysts
        received via ``create_X_analyst(llm)``).
    tools:
        Either a list of LangChain tools, or a callable ``(state) -> list``
        that returns the tool list for this run. A callable lets analysts
        whose tool set depends on the market (e.g. ``news_analyst`` for
        ``cn_a``) express that without special-casing in the factory.
    system_message:
        Either a string, or a callable ``(state) -> str`` that builds the
        system message for this run. A callable is used when the message
        incorporates market/language/style instructions derived from state.
    report_key:
        The state key under which the final report is stored
        (e.g. ``"market_report"``).

    Returns
    -------
    Callable[[state], dict]
        A graph node with the same signature and return shape as the original
        inlined analyst nodes.
    """

    def _resolve(value, state):
        # Plain values (list of tools, system-message string) are used as-is;
        # callables are invoked with ``state`` so market/style-dependent
        # values are computed at node-execution time, exactly as the inlined
        # originals did.
        return value(state) if callable(value) else value

    def analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)

        resolved_tools = _resolve(tools, state)
        resolved_system_message = _resolve(system_message, state)

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

        prompt = prompt.partial(system_message=resolved_system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in resolved_tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(resolved_tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            report_key: report,
        }

    return analyst_node
