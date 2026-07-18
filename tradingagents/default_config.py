import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "TRADINGAGENTS_LLM_PROVIDER":         "llm_provider",
    "TRADINGAGENTS_DEEP_THINK_LLM":       "deep_think_llm",
    "TRADINGAGENTS_QUICK_THINK_LLM":      "quick_think_llm",
    "TRADINGAGENTS_LLM_BACKEND_URL":      "backend_url",
    "TRADINGAGENTS_OUTPUT_LANGUAGE":      "output_language",
    "TRADINGAGENTS_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "TRADINGAGENTS_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "TRADINGAGENTS_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "TRADINGAGENTS_BENCHMARK_TICKER":     "benchmark_ticker",
    "TRADINGAGENTS_TEMPERATURE":          "temperature",
    "TRADINGAGENTS_NEWS_BODY_SNIPPET_ITEMS": "news_body_snippet_items",
    "TRADINGAGENTS_NEWS_BODY_SNIPPET_CHARS": "news_body_snippet_chars",
    "STOCKMANAGER_MCP_URL":               "stockmanager_mcp_url",
    "STOCKMANAGER_MCP_ENABLED":           "stockmanager_mcp_enabled",
    "STOCKMANAGER_MCP_TIMEOUT":           "stockmanager_mcp_timeout",
    "TRADINGAGENTS_SCHEDULER_ENABLED":    "scheduler_enabled",
    "TRADINGAGENTS_CROSS_SYMBOL_MINER_ENABLED": "cross_symbol_miner_enabled",
    "TRADINGAGENTS_CROSS_SYMBOL_MINER_NEUTRAL_ENABLED": "cross_symbol_miner_neutral_enabled",
    "TRADINGAGENTS_TICKER_NAME_BACKFILL_ENABLED": "ticker_name_backfill_enabled",
    "TRADINGAGENTS_MCP_STOCKMANAGER_DIR": "mcp_stockmanager_dir",
    "TRADINGAGENTS_INVESTMENT_STYLE":     "investment_style",
    "TRADINGAGENTS_API_AUTH_TOKEN":       "api_auth_token",
    "TRADINGAGENTS_DAILY_PIPELINE_LLM_REVIEW_ENABLED": "daily_pipeline_llm_review_enabled",
    "TRADINGAGENTS_DAILY_PIPELINE_LLM_REVIEW_LIMIT": "daily_pipeline_llm_review_limit",
    "TRADINGAGENTS_DAILY_PIPELINE_LLM_REVIEW_LESSON_EXTRA": "daily_pipeline_llm_review_lesson_extra",
    "TRADINGAGENTS_DAILY_PIPELINE_BOARD_FILTER": "daily_pipeline_board_filter",
    "TRADINGAGENTS_DAILY_PIPELINE_DEEP_ANALYSIS_ENABLED": "daily_pipeline_deep_analysis_enabled",
    "TRADINGAGENTS_DAILY_PIPELINE_DEEP_ANALYSIS_LIMIT": "daily_pipeline_deep_analysis_limit",
}


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value."""
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply TRADINGAGENTS_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "openai",
    "deep_think_llm": "gpt-5.5",
    "quick_think_llm": "gpt-5.4-mini",
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Sampling temperature, forwarded to every provider when set. None leaves
    # each provider at its own default. Lower values reduce run-to-run
    # variation on models that honor it; reasoning models largely ignore it
    # and no setting makes LLM output bit-identical across runs (see README).
    "temperature": None,
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "English",
    # When True (default) and output_language is the default "English", A-share
    # runs auto-switch their user-facing reports to Chinese.
    "auto_switch_language_for_cn": True,
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    "analyst_concurrency_limit": 1,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "news_body_snippet_items": 5,         # include body snippets for top N ticker-news items when vendor provides them
    "news_body_snippet_chars": 600,       # per-item character cap for fetched article body snippets
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Data vendor configuration
    # Category-level configuration supports market-aware mapping:
    #   {"us": "yfinance", "cn_a": "akshare"}
    # Plain string values are accepted for backwards compatibility and
    # apply to all markets (see get_vendor in dataflows/interface.py).
    "data_vendors": {
        "core_stock_apis": {"us": "yfinance", "cn_a": "akshare, tushare"},
        "technical_indicators": {"us": "yfinance", "cn_a": "akshare, tushare"},
        "fundamental_data": {"us": "yfinance", "cn_a": "tushare"},
        "news_data": {"us": "yfinance", "cn_a": "akshare"},
        "social_sentiment": {"cn_a": "akshare"},
        "company_announcements": {"cn_a": "akshare"},
        "macro_data": {"us": "fred", "cn_a": "akshare"},
        "cn_market_specific": {"cn_a": "akshare"},
        "prediction_markets": {"us": "polymarket"},
    },
    # Tool-level configuration (takes precedence over category-level).
    # Values may also be market-aware dicts.
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
    # Default market used when a tool has no ticker argument (e.g. global news)
    # and the agent state doesn't provide one. Options: "us", "cn_a".
    "default_market": "us",
    # CN data providers
    "tushare_token": None,          # reads TUSHARE_TOKEN env var when None
    "akshare_rate_limit": 1.0,      # QPS
    "tushare_rate_limit": 3.0,      # QPS
    "cn_trading_calendar_cache": os.path.join(_TRADINGAGENTS_HOME, "cache", "cn_trade_cal.csv"),
    "trading_time_timezone": "Asia/Shanghai",
    "cn_a_close_data_available_time": "15:30",
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",       # NSE India (Nifty 50)
        ".BO":  "^BSESN",      # BSE India (Sensex)
        ".T":   "^N225",       # Tokyo (Nikkei 225)
        ".HK":  "^HSI",        # Hong Kong (Hang Seng)
        ".L":   "^FTSE",       # London (FTSE 100)
        ".TO":  "^GSPTSE",     # Toronto (TSX Composite)
        ".AX":  "^AXJO",       # Australia (ASX 200)
        ".SS":  "000001.SS",   # Shanghai (SSE Composite, Yahoo suffix)
        ".SH":  "000001.SS",   # Shanghai (AKShare/TuShare suffix)
        ".SZ":  "399001.SZ",   # Shenzhen (SZSE Component)
        "":     "SPY",         # default for US-listed tickers (no suffix)
    },
    # -------------------------------------------------------------------
    # MCP (Model Context Protocol) integration
    # -------------------------------------------------------------------
    # StockManager MCP service. TradingAgents connects to this independently
    # managed localhost service over HTTP/Streamable MCP.
    "stockmanager_mcp_url": os.getenv("STOCKMANAGER_MCP_URL", "http://127.0.0.1:8765/mcp"),
    "stockmanager_mcp_enabled": True,
    "stockmanager_mcp_timeout": 120.0,
    "stockmanager_mcp_sse_read_timeout": 300.0,
    "stockmanager_mcp_health_timeout": 2.0,
    "scheduler_enabled": True,
    "ticker_name_backfill_enabled": True,
    # API auth: when set (env TRADINGAGENTS_API_AUTH_TOKEN), REST + WS requests
    # must carry this token (Bearer header for REST, ?token= for WS). Empty by
    # default so local desktop usage is unaffected; set it when exposing the
    # API beyond localhost.
    "api_auth_token": os.getenv("TRADINGAGENTS_API_AUTH_TOKEN", ""),
    # Legacy stdio path kept only for compatibility with older configs.
    "mcp_stockmanager_dir": os.path.expanduser("~/Documents/develop/StockManager"),
    # -------------------------------------------------------------------
    # User investment preferences (cross-cutting, injected into agents)
    # -------------------------------------------------------------------
    # One of "short_term" (短线), "medium_term" (中线), "long_term" (长线).
    # Controls analyst focus, factor weights, and decision framework wording.
    "investment_style": "long_term",
    # Daily A-share screening fusion. Quant ranking always comes first; the
    # quick LLM only reviews the top N candidates to keep latency and cost
    # bounded.
    "daily_pipeline_llm_review_enabled": True,
    "daily_pipeline_llm_review_limit": 8,
    # Candidates ranked beyond the review limit but matching an ACTIVE strategy
    # lesson are pulled into the review window (bounded by this cap) so the
    # reflection loop's lessons actually influence matching candidates.
    "daily_pipeline_llm_review_lesson_extra": 4,
    # all | main_board | dual_growth_only. main_board excludes STAR/ChiNext
    # to avoid repeated high-beta 双创 recommendations when the user wants
    # steadier A-share main-board candidates.
    "daily_pipeline_board_filter": "all",
    # Optional deep-analysis chaining: after quant ranking + LLM review, run the
    # heavyweight multi-agent StockAnalysisSkill on the Top N candidates and
    # write its structured conclusion back onto the candidate payload under
    # ``deep_analysis``. Off by default because it runs the full agent graph
    # per stock (minutes + tokens each); enable for a nightly deep pass on the
    # very top names. The limit is intentionally small (1) so latency/cost stay
    # bounded even when enabled.
    "daily_pipeline_deep_analysis_enabled": False,
    "daily_pipeline_deep_analysis_limit": 1,
    # LLM-based intent routing (Phase 3). When enabled, messages that don't
    # match regex patterns with high confidence are forwarded to LLM for
    # tool_use-based intent recognition. Disabled by default.
    "llm_routing_enabled": False,
    # Cross-symbol pattern mining. When enabled, the reflection batch runs a
    # statistical pattern discovery step after processing pending cases.
    # Patterns are saved as strategy_lessons with lesson_type="cross_symbol_pattern".
    # Enabled by default so high-confidence patterns auto-promote after the
    # daily 16:30 reflection batch; override with
    # TRADINGAGENTS_CROSS_SYMBOL_MINER_ENABLED=false to disable.
    "cross_symbol_miner_enabled": True,
    "cross_symbol_miner_min_samples": 5,
    "cross_symbol_miner_min_lift": 0.15,
    "cross_symbol_miner_lookback_days": 30,
    # Neutral channel of the miner: promotes WATCHLIST/HOLD/MONITOR patterns
    # by consistent excess-over-benchmark return (the win-rate gate cannot see
    # neutral cases because was_correct is None). Only runs when the master
    # cross_symbol_miner switch above is enabled.
    "cross_symbol_miner_neutral_enabled": True,
    "cross_symbol_miner_min_excess": 0.05,
    "cross_symbol_miner_min_consistency": 0.6,
    # Neutral patterns are coarser (industry/factor level) and already gated by
    # excess magnitude + same-sign consistency, so they use a lower minimum
    # sample count than the directional win-rate channel (which stays at
    # cross_symbol_miner_min_samples).
    "cross_symbol_miner_neutral_min_samples": 4,
    # Regime guardrail: a neutral pattern must span at least this many distinct
    # ISO weeks of signal dates to promote. A single sector-wide selloff or a
    # one-day batch produces a strong-but-spurious excess concentrated in one
    # window; requiring recurrence across >=N weeks keeps market/sector-regime
    # episodes from being minted as permanent lessons. (Sector-beta
    # decomposition against an industry index is a further TODO.)
    "cross_symbol_miner_neutral_min_periods": 2,
})
