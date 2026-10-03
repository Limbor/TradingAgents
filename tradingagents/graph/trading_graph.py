# TradingAgents/graph/trading_graph.py

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf
from langgraph.prebuilt import ToolNode

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_announcements,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_global_news,
    get_income_statement,
    get_indicators,
    get_insider_transactions,
    get_lhb_detail,
    get_limit_status,
    get_macro_calendar,
    get_macro_indicators,
    get_margin_balance,
    get_market_structure_snapshot,
    get_news,
    get_northbound_flow,
    get_prediction_markets,
    # CN market tools
    get_social_sentiment,
    get_stock_data,
    get_theme_heat,
    get_unlock_schedule,
    get_verified_market_snapshot,
    resolve_instrument_identity,
)
from tradingagents.agents.utils.memory import TradingMemoryLog

# Import the abstract tool methods from agent_utils
from tradingagents.core.agent_runtime import (
    AgentContext,
    current_context,
    runtime_model,
    use_context,
)
from tradingagents.core.model_policy import freeze_model_config, provider_kwargs
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.symbol_utils import detect_market
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients import create_llm_client
from tradingagents.reporting import write_report_tree

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .propagation import Propagator
from .reflection import Reflector
from .setup import GraphSetup
from .signal_processing import SignalProcessor

logger = logging.getLogger(__name__)


def _yahoo_close_series(symbol: str, start: str, end: str) -> list[float]:
    """Close-price series for ``symbol`` from yfinance over [start, end].

    Returns an empty list on any failure so callers can fall back to None alpha
    rather than raising. Kept module-level so it can be reused/mocked without a
    class instance.
    """
    try:
        df = yf.Ticker(symbol).history(start=start, end=end)
        return [float(c) for c in df["Close"].tolist()]
    except Exception as exc:
        logger.debug("yfinance close fetch failed for %s: %s", symbol, exc)
        return []


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=("market", "social", "news", "fundamentals"),
        debug=False,
        config: dict[str, Any] = None,
        callbacks: list | None = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = freeze_model_config(config or DEFAULT_CONFIG)
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = runtime_model(deep_client.get_llm(), "Specialist", self.config, "deep")
        self.quick_thinking_llm = runtime_model(quick_client.get_llm(), "Specialist", self.config)

        self.memory_log = TradingMemoryLog(self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts, template=self.config.get("analysis_template", "full"))
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self) -> dict[str, Any]:
        return provider_kwargs(self.config)

    def _create_tool_nodes(self) -> dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                    # Deterministic verification snapshot (bound to the analyst
                    # LLM and required by its prompt; must be executable here or
                    # the call fails and the model reports it "unavailable").
                    get_verified_market_snapshot,
                    get_market_structure_snapshot,
                    get_theme_heat,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                    # A-share social / retail sentiment
                    get_social_sentiment,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                    get_macro_indicators,
                    get_prediction_markets,
                    # A-share macro calendar
                    get_macro_calendar,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                    # A-share microstructure tools (only used when market == cn_a)
                    get_announcements,
                    get_lhb_detail,
                    get_limit_status,
                    get_margin_balance,
                    get_northbound_flow,
                    get_unlock_schedule,
                ]
            ),
        }

    def _resolve_benchmark(self, ticker: str) -> str:
        """Pick the benchmark ticker for alpha calculation against ``ticker``.

        ``config["benchmark_ticker"]`` overrides everything when set; otherwise
        the suffix map matches the ticker's exchange suffix (e.g. ``.T`` for
        Tokyo). US-listed tickers without a dotted suffix fall through to the
        empty-suffix entry (SPY by default). Unrecognised suffixes (including
        US tickers with dots like ``BRK.B``) also fall back to the empty-suffix
        entry, which is the right default because the alpha calculation works
        in USD.
        """
        config = self.config if isinstance(getattr(self, "config", None), dict) else {}
        explicit = config.get("benchmark_ticker")
        if explicit:
            return explicit
        # A-share defaults: Shanghai Composite for .SH, Shenzhen Component for
        # .SZ. The config-supplied benchmark_map can still override per-suffix.
        default_map = {".SH": "000001.SH", ".SZ": "399001.SZ", ".BJ": "000001.SH"}
        ticker_upper = ticker.upper()
        for suffix, bench in default_map.items():
            if ticker_upper.endswith(suffix):
                return bench
        benchmark_map = config.get("benchmark_map", {})
        for suffix, benchmark in benchmark_map.items():
            if suffix and ticker_upper.endswith(suffix.upper()):
                return benchmark
        return benchmark_map.get("", "SPY")

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5,
        benchmark: str | None = None,
    ) -> tuple[float | None, float | None, int | None]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        ``benchmark`` is the index used as the alpha baseline. When omitted it
        is resolved from the ticker market. Returns ``(raw_return, alpha_return,
        actual_holding_days)`` or ``(None, None, None)`` if price data is
        unavailable (too recent, delisted, or network error).

        A-share stocks are fetched from the local AKShare OHLCV (the same source
        the analysis priced off) because yfinance's A-share quotes are frequently
        delayed, missing, or inconsistently adjusted — which previously left
        pending reflection entries unresolved forever. CN indices used as
        benchmarks are still fetched via yfinance (they are more reliable there
        than individual A-share stocks); if the benchmark is unavailable, alpha
        is returned as None.
        """
        from tradingagents.dataflows.symbol_utils import detect_market, normalize_symbol

        try:
            # Call the implementation directly so this utility also behaves
            # predictably when invoked unbound with a lightweight test double.
            benchmark = benchmark or TradingAgentsGraph._resolve_benchmark(self, ticker)
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            market = detect_market(ticker)
            if market == "cn_a":
                stock_closes = self._cn_close_series(ticker, trade_date, end_str)
            else:
                stock_closes = _yahoo_close_series(normalize_symbol(ticker), trade_date, end_str)
            bench_closes = _yahoo_close_series(benchmark, trade_date, end_str)

            if len(stock_closes) < 2:
                return None, None, None

            bench_len = len(bench_closes)
            if bench_len >= 2:
                actual_days = min(holding_days, len(stock_closes) - 1, bench_len - 1)
            else:
                actual_days = min(holding_days, len(stock_closes) - 1)

            raw = float(
                (stock_closes[actual_days] - stock_closes[0]) / stock_closes[0]
            )
            if bench_len >= 2:
                bench_ret = float(
                    (bench_closes[actual_days] - bench_closes[0]) / bench_closes[0]
                )
                alpha = raw - bench_ret
            else:
                alpha = None
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s vs %s (will retry next run): %s",
                ticker, trade_date, benchmark, e,
            )
            return None, None, None

    def _cn_close_series(self, ticker: str, start: str, end: str) -> list[float]:
        """Close-price series for an A-share ticker via AKShare, in [start, end].

        Reuses ``load_ohlcv_cn`` (the same qfq OHLCV the analysis used) so the
        realized return is measured against the same adjusted prices.
        """
        try:
            import pandas as pd

            from tradingagents.dataflows.akshare_stock import load_ohlcv_cn

            df = load_ohlcv_cn(ticker, end)  # filtered to <= end internally
            start_dt = pd.to_datetime(start)
            df = df[df["Date"] >= start_dt]
            return [float(c) for c in df["Close"].tolist()]
        except Exception as exc:
            logger.debug("AKShare close fetch failed for %s: %s", ticker, exc)
            return []

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        DEPRECATED — intentionally a no-op. Reflection is now owned by
        ``core/reflection.py::ReflectionEngine.run_reflection_batch`` (daily
        16:30 scheduler / daily_review trigger), which produces the deep
        attribution + strategy lesson + DB record. Previously this method
        ran a shallow synchronous reflection on the same memory_log pending
        entries, which flipped the ``| pending]`` tag first and caused the
        core batch to skip the entry — so the decision never got attribution
        or a DB ``reflections`` row. New pending entries are still written by
        ``store_decision``; they stay pending until the core batch resolves
        them. ``get_past_context`` will return the raw decision (no reflection)
        until then, which is the intended behavior.
        """
        return

    def resolve_instrument_context(self, ticker: str, asset_type: str = "stock") -> str:
        """Resolve ticker identity once and return the full instrument context.

        Deterministic yfinance lookup (cached, fail-open) injected into a
        context string so every agent anchors to the real company instead of
        hallucinating one from the price chart (#814). Both the propagate()
        path and the CLI call this so the resolved identity reaches the whole
        graph regardless of entry point.
        """
        identity = resolve_instrument_identity(ticker)
        return build_instrument_context(ticker, asset_type, identity)

    def _merge_memory_context(self, past_context: str) -> str:
        extra = str(self.config.get("memory_extra_context") or "").strip()
        if not extra:
            return past_context
        if past_context:
            return f"{past_context}\n\n{extra}"
        return extra

    def save_reports(self, final_state: dict[str, Any], ticker: str, save_path: str | Path | None = None) -> Path:
        """Write a completed graph state as a markdown report tree."""

        if save_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = (
                Path(self.config["results_dir"])
                / "reports"
                / f"{safe_ticker_component(ticker)}_{stamp}"
            )
        return write_report_tree(final_state, ticker, save_path)

    def propagate(self, company_name, trade_date, asset_type: str = "stock"):
        context = current_context() or AgentContext.root(self.config, root_id=self.config.get("run_id"),
                                                       symbols=[company_name], as_of_date=str(trade_date), db=self.config.get("db"))
        with use_context(context):
            return TradingAgentsGraph._propagate(self, company_name, trade_date, asset_type)

    def _propagate(self, company_name, trade_date, asset_type: str = "stock"):
        """Run the trading agents graph for a company on a specific date.

        ``asset_type`` selects between the stock pipeline (default) and the
        crypto pipeline (``"crypto"``) shipped in #567 — the CLI auto-detects
        from the ticker; programmatic callers pass it explicitly. When
        ``checkpoint_enabled`` is set in config, the graph is recompiled with
        a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.
        """
        self.ticker = company_name

        # Resolve any pending memory-log entries for this ticker before the pipeline runs.
        self._resolve_pending_entries(company_name)

        # Recompile with a checkpointer if the user opted in.
        if self.config.get("checkpoint_enabled"):
            self._checkpointer_ctx = get_checkpointer(
                self.config["data_cache_dir"], company_name
            )
            saver = self._checkpointer_ctx.__enter__()
            self.graph = self.workflow.compile(checkpointer=saver)

            step = checkpoint_step(
                self.config["data_cache_dir"], company_name, str(trade_date),
                str(self.config.get("checkpoint_run_id") or self.config.get("run_id") or "") or None,
            )
            if step is not None:
                logger.info(
                    "Resuming from step %d for %s on %s", step, company_name, trade_date
                )
            else:
                logger.info("Starting fresh for %s on %s", company_name, trade_date)

        try:
            return self._run_graph(company_name, trade_date, asset_type=asset_type)
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()


    def _run_graph(self, company_name, trade_date, asset_type: str = "stock"):
        """Execute the graph and write the resulting state to disk and memory log."""
        # Initialize state — inject memory log context for PM and the
        # deterministically resolved instrument identity for all agents.
        past_context = self._merge_memory_context(self.memory_log.get_past_context(company_name, as_of_date=str(trade_date)))
        instrument_context = self.resolve_instrument_context(company_name, asset_type)
        market = detect_market(company_name)
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
            market=market,
            investment_style=str(self.config.get("investment_style") or ""),
            research_question=str(self.config.get("research_question") or ""),
        )
        args = self.propagator.get_graph_args()

        init_agent_state["research_reuse"] = self.config.get("research_reuse") or {}
        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        if self.config.get("checkpoint_enabled"):
            checkpoint_run_id = str(
                self.config.get("checkpoint_run_id") or self.config.get("run_id") or ""
            ) or None
            tid = thread_id(company_name, str(trade_date), checkpoint_run_id)
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        if self.debug:
            trace = []
            last_printed = None
            for chunk in self.graph.stream(init_agent_state, **args):
                if chunk["messages"]:
                    msg = chunk["messages"][-1]
                    # Nodes after the trader don't append to messages, so the
                    # same trailing message repeats across chunks. Print it only
                    # when it changes (#1027); the trace/state merge is unchanged.
                    signature = (type(msg).__name__, getattr(msg, "content", None))
                    if signature != last_printed:
                        msg.pretty_print()
                        last_printed = signature
                    trace.append(chunk)
            # Streamed chunks are per-node deltas. Merge them so the returned
            # state matches what graph.invoke() yields in the non-debug path.
            final_state = {}
            for chunk in trace:
                final_state.update(chunk)
        else:
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection.
        self.curr_state = final_state

        # Log state to disk.
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        self.memory_log.store_decision(
            ticker=company_name,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )

        # Clear checkpoint on successful completion to avoid stale state.
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date),
                str(self.config.get("checkpoint_run_id") or self.config.get("run_id") or "") or None,
            )

        return final_state, self.process_signal(final_state["final_trade_decision"])

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
            "structured_portfolio_decision": final_state.get("structured_portfolio_decision"),
        }

        # Save to file. Reject ticker values that would escape the
        # results directory when joined as a path component.
        safe_ticker = safe_ticker_component(self.ticker)
        directory = Path(self.config["results_dir"]) / safe_ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)

    async def astream_propagate(self, ticker, date, selected_analysts=None, asset_type="stock"):
        context = current_context() or AgentContext.root(self.config, root_id=self.config.get("run_id"),
                                                       symbols=[ticker], as_of_date=date, db=self.config.get("db"))
        stream = self._astream_propagate(ticker, date, selected_analysts, asset_type)
        try:
            while True:
                try:
                    with use_context(context):
                        event = await stream.__anext__()
                        context = current_context()
                except StopAsyncIteration:
                    break
                yield event
        finally:
            with use_context(context):
                await stream.aclose()

    async def _astream_propagate(
        self,
        ticker: str,
        date: str,
        selected_analysts: list[str] | None = None,
        asset_type: str = "stock",
    ):
        """Async streaming version of propagate() that yields events.

        Uses LangGraph's astream_events API for fine-grained event streaming.
        Report sections are extracted from on_chain_end events as agents
        complete their work.

        Yields:
            dict with 'type' and 'data' keys for real-time event consumption.
        """
        if selected_analysts is None:
            selected_analysts = ["market", "social", "news", "fundamentals"]

        REPORT_KEYS = {
            "market_report", "sentiment_report", "news_report",
            "fundamentals_report", "investment_plan",
            "trader_investment_plan", "final_trade_decision",
        }

        AGENT_NAMES = {
            "Market Analyst", "Sentiment Analyst", "News Analyst",
            "Fundamentals Analyst", "Bull Researcher", "Bear Researcher",
            "Research Manager", "Trader", "Aggressive Analyst",
            "Conservative Analyst", "Neutral Analyst", "Portfolio Manager",
        }

        self._resolve_pending_entries(ticker)
        self.ticker = ticker

        instrument_context = self.resolve_instrument_context(ticker, asset_type)
        past_context = self._merge_memory_context(self.memory_log.get_past_context(ticker, as_of_date=str(date)))
        market = detect_market(ticker)

        init_agent_state = self.propagator.create_initial_state(
            ticker,
            date,
            asset_type=asset_type,
            past_context=past_context,
            instrument_context=instrument_context,
            market=market,
            investment_style=str(self.config.get("investment_style") or ""),
            research_question=str(self.config.get("research_question") or ""),
        )

        init_agent_state["research_reuse"] = self.config.get("research_reuse") or {}
        workflow = self.graph_setup.setup_graph(selected_analysts, template=self.config.get("analysis_template", "full"))
        args = self.propagator.get_graph_args()

        # Mirror propagate()'s checkpointer handling so the Skill entry point
        # (astream_propagate) also benefits from resume-after-crash when the user
        # opts in via checkpoint_enabled. Previously this path compiled the graph
        # without a saver, silently ignoring the config.
        checkpointer_ctx = None
        if self.config.get("checkpoint_enabled"):
            checkpointer_ctx = get_checkpointer(self.config["data_cache_dir"], ticker)
            saver = checkpointer_ctx.__enter__()
            graph = workflow.compile(checkpointer=saver)
            checkpoint_run_id = str(
                self.config.get("checkpoint_run_id") or self.config.get("run_id") or ""
            ) or None
            tid = thread_id(ticker, str(date), checkpoint_run_id)
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid
            step = checkpoint_step(
                self.config["data_cache_dir"], ticker, str(date), checkpoint_run_id
            )
            if step is not None:
                logger.info("Resuming from step %d for %s on %s (streaming)", step, ticker, date)
            else:
                logger.info("Starting fresh for %s on %s (streaming)", ticker, date)
        else:
            graph = workflow.compile()

        final_sections: dict[str, str] = {}
        active_tools: dict[str, dict[str, Any]] = {}
        tool_agents = {"tools_market": "Market Analyst", "tools_social": "Sentiment Analyst",
                       "tools_news": "News Analyst", "tools_fundamentals": "Fundamentals Analyst"}
        structured_portfolio_decision: dict[str, Any] | None = None
        specialist_results: list[dict] = []

        try:
            async for event in graph.astream_events(
                init_agent_state,
                version="v2",
                config=args.get("config", {}),
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                if kind == "on_chain_start" and name in AGENT_NAMES:
                    yield {
                        "type": "agent_status",
                        "data": {"agent": name, "status": "running", "activity_id": str(event.get("run_id") or name)},
                    }

                elif kind == "on_chain_end" and name in AGENT_NAMES:
                    yield {
                        "type": "agent_status",
                        "data": {"agent": name, "status": "completed", "activity_id": str(event.get("run_id") or name)},
                    }
                    # Extract report sections from the node's output
                    output = data.get("output")
                    if isinstance(output, dict):
                        for receipt in output.get("specialist_results", []):
                            stats = receipt.get("context_stats") or {}
                            if stats.get("saved_tokens_estimate", 0) > 0:
                                yield {"type": "context_compacted", "data": {"agent": name, "context_stats": stats}}
                        specialist_results.extend(output.get("specialist_results", []))
                        raw_decision = output.get("structured_portfolio_decision")
                        if isinstance(raw_decision, dict):
                            structured_portfolio_decision = raw_decision
                        for key in REPORT_KEYS:
                            if key in output and output[key]:
                                final_sections[key] = output[key]
                                yield {
                                    "type": "report_chunk",
                                    "data": {
                                        "section": key,
                                        "content": output[key],
                                        "is_final": key == "final_trade_decision",
                                    },
                                }

                elif kind == "on_tool_start":
                    call_id = str(event.get("run_id") or name)
                    active_tools[call_id] = {
                        "tool": name or "unknown", "args": data.get("input", {}),
                        "activity_id": call_id,
                        "agent": tool_agents.get((event.get("metadata") or {}).get("langgraph_node")) or
                                 ((event.get("metadata") or {}).get("langgraph_node") if (event.get("metadata") or {}).get("langgraph_node") in AGENT_NAMES else None),
                    }
                    yield {
                        "type": "tool_call",
                        "data": {**active_tools[call_id], "status": "running"},
                    }
                elif kind in {"on_tool_end", "on_tool_error"}:
                    call_id = str(event.get("run_id") or name)
                    call = active_tools.pop(call_id, None)
                    if call is not None:
                        output = data.get("output")
                        output_status = output.get("status") if isinstance(output, dict) else getattr(output, "status", None)
                        failed = kind == "on_tool_error" or output_status == "error"
                        yield {"type": "tool_call", "data": {
                            **call, "status": "failed" if failed else "completed",
                        }}

            # Emit complete report with all sections
            if final_sections:
                yield {
                    "type": "report_complete",
                    "data": {
                        "sections": final_sections,
                        "ticker": ticker,
                        "date": date,
                        "structured_portfolio_decision": structured_portfolio_decision,
                        "specialist_results": specialist_results,
                    },
                }

            # Store decision for deferred reflection
            if "final_trade_decision" in final_sections:
                self.memory_log.store_decision(
                    ticker=ticker,
                    trade_date=date,
                    final_trade_decision=final_sections["final_trade_decision"],
                )

            # Clear checkpoint on successful completion to avoid stale state.
            if checkpointer_ctx is not None:
                clear_checkpoint(
                    self.config["data_cache_dir"], ticker, str(date), checkpoint_run_id
                )
        finally:
            if checkpointer_ctx is not None:
                checkpointer_ctx.__exit__(None, None, None)
