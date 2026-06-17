import logging

from .alpha_vantage import (
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_global_news as get_alpha_vantage_global_news,
    get_income_statement as get_alpha_vantage_income_statement,
    get_indicator as get_alpha_vantage_indicator,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_stock as get_alpha_vantage_stock,
)
from .config import get_config
from .errors import (
    NoMarketDataError,
    VendorNotConfiguredError,
    VendorRateLimitError,
)
from .fred import get_macro_data as get_fred_macro_data
from .polymarket import get_prediction_markets as get_polymarket_prediction_markets
from .y_finance import (
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_fundamentals as get_yfinance_fundamentals,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
    get_stock_stats_indicators_window,
    get_YFin_data_online,
)
from .yfinance_news import get_global_news_yfinance, get_news_yfinance

# AKShare vendor (A-share / CN market)
from .akshare_common import AKShareRateLimitError
from .akshare_stock import (
    get_stock as get_akshare_stock,
    get_indicator as get_akshare_indicator,
)
from .akshare_news import (
    get_news as get_akshare_news,
    get_global_news as get_akshare_global_news,
    get_insider_transactions as get_akshare_insider_transactions,
)
from .akshare_social import get_social_sentiment as get_akshare_social_sentiment
from .akshare_announcements import get_announcements as get_akshare_announcements
from .akshare_macro import get_macro_calendar as get_akshare_macro_calendar
from .akshare_cn_specific import (
    get_limit_status as get_akshare_limit_status,
    get_northbound_flow as get_akshare_northbound_flow,
    get_margin_balance as get_akshare_margin_balance,
    get_unlock_schedule as get_akshare_unlock_schedule,
)

# TuShare vendor (A-share fundamentals)
from .tushare_common import TuShareRateLimitError
from .tushare_fundamentals import (
    get_fundamentals as get_tushare_fundamentals,
    get_balance_sheet as get_tushare_balance_sheet,
    get_cashflow as get_tushare_cashflow,
    get_income_statement as get_tushare_income_statement,
)
from .tushare_stock import (
    get_stock as get_tushare_stock,
    get_indicator as get_tushare_indicator,
)

from .symbol_utils import detect_market

logger = logging.getLogger(__name__)

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    },
    "macro_data": {
        "description": "Macroeconomic indicators (rates, inflation, labor, growth)",
        "tools": [
            "get_macro_indicators",
            "get_macro_calendar",
        ]
    },
    "social_sentiment": {
        "description": "Social / retail-investor sentiment (A-share only)",
        "tools": [
            "get_social_sentiment",
        ]
    },
    "company_announcements": {
        "description": "Official company filings / announcements",
        "tools": [
            "get_announcements",
        ]
    },
    "cn_market_specific": {
        "description": "A-share microstructure: limit-up/down, northbound, margin, unlock",
        "tools": [
            "get_limit_status",
            "get_northbound_flow",
            "get_margin_balance",
            "get_unlock_schedule",
        ]
    },
    "prediction_markets": {
        "description": "Market-implied probabilities for forward-looking events",
        "tools": [
            "get_prediction_markets",
        ]
    }
}

VENDOR_LIST = [
    "yfinance",
    "fred",
    "polymarket",
    "alpha_vantage",
    "akshare",
    "tushare",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
        "akshare": get_akshare_stock,
        "tushare": get_tushare_stock,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
        "akshare": get_akshare_indicator,
        "tushare": get_tushare_indicator,
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        "tushare": get_tushare_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "tushare": get_tushare_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "tushare": get_tushare_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "tushare": get_tushare_income_statement,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
        "akshare": get_akshare_news,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
        "akshare": get_akshare_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
        "akshare": get_akshare_insider_transactions,
    },
    # macro_data
    "get_macro_indicators": {
        "fred": get_fred_macro_data,
    },
    "get_macro_calendar": {
        "akshare": get_akshare_macro_calendar,
    },
    # social_sentiment (A-share)
    "get_social_sentiment": {
        "akshare": get_akshare_social_sentiment,
    },
    # company_announcements (A-share)
    "get_announcements": {
        "akshare": get_akshare_announcements,
    },
    # cn_market_specific (A-share)
    "get_limit_status": {
        "akshare": get_akshare_limit_status,
    },
    "get_northbound_flow": {
        "akshare": get_akshare_northbound_flow,
    },
    "get_margin_balance": {
        "akshare": get_akshare_margin_balance,
    },
    "get_unlock_schedule": {
        "akshare": get_akshare_unlock_schedule,
    },
    # prediction_markets
    "get_prediction_markets": {
        "polymarket": get_polymarket_prediction_markets,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def _resolve_vendor_value(value, market: str) -> str | None:
    """Resolve a vendor-config value against the given market.

    Supports three forms:
      - string: applied to all markets (back-compat)
      - dict keyed by market: ``{"us": "yfinance", "cn_a": "akshare"}``
      - None: no configured vendor for this market
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get(market)
    return None


def get_vendor(category: str, method: str | None = None, market: str = "us") -> str:
    """Get the configured vendor for a data category or specific tool method.

    Tool-level configuration takes precedence over category-level.
    Values may be plain strings (back-compat) or market-aware dicts.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            resolved = _resolve_vendor_value(tool_vendors[method], market)
            if resolved is not None:
                return resolved

    # Fall back to category-level configuration
    cat_value = config.get("data_vendors", {}).get(category)
    resolved = _resolve_vendor_value(cat_value, market)
    return resolved or "default"


# Methods whose first positional argument is a ticker (used for market detection)
_TICKER_FIRST_METHODS = {
    "get_stock_data", "get_indicators", "get_fundamentals",
    "get_balance_sheet", "get_cashflow", "get_income_statement",
    "get_news", "get_insider_transactions", "get_social_sentiment",
    "get_announcements", "get_limit_status", "get_margin_balance",
    "get_unlock_schedule",
}

# Rate-limit exceptions from CN vendors (caught during fallback chain)
_FALLBACK_EXCEPTIONS: tuple = (AKShareRateLimitError, TuShareRateLimitError)

# Vendors that only serve US market data — excluded when market == "cn_a"
_US_ONLY_VENDORS = {"yfinance"}

def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support."""
    # Infer market from ticker argument
    config = get_config()
    default_market = config.get("default_market", "us")
    market = default_market
    if method in _TICKER_FIRST_METHODS and args:
        first = args[0]
        if isinstance(first, str) and first:
            try:
                market = detect_market(first)
            except Exception:
                market = default_market

    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method, market=market)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    all_available_vendors = list(VENDOR_METHODS[method].keys())

    # The configured vendor list IS the chain: we do NOT silently fall back to
    # vendors the user did not choose (#988/#289) — that returned data from an
    # unexpected source and caused cross-vendor inconsistencies. For multi-vendor
    # fallback, list them in order, e.g. data_vendors="yfinance,alpha_vantage".
    # The "default" sentinel (no explicit config) uses all available vendors.
    explicit = [v for v in primary_vendors if v and v != "default"]
    if explicit:
        vendor_chain = [v for v in explicit if v in VENDOR_METHODS[method]]
        if not vendor_chain:
            raise ValueError(
                f"Configured vendor(s) {explicit} not available for '{method}'. "
                f"Available: {all_available_vendors}."
            )
    else:
        vendor_chain = all_available_vendors

    # Exclude US-only vendors when analyzing CN market tickers
    if market == "cn_a":
        vendor_chain = [v for v in vendor_chain if v not in _US_ONLY_VENDORS]
        if not vendor_chain:
            vendor_chain = [v for v in all_available_vendors if v not in _US_ONLY_VENDORS]

    last_no_data: NoMarketDataError | None = None
    first_error: Exception | None = None
    for vendor in vendor_chain:
        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            return impl_func(*args, **kwargs)
        except VendorRateLimitError:
            logger.warning("Vendor %r rate-limited for %s; trying next vendor.", vendor, method)
            continue
        except _FALLBACK_EXCEPTIONS:
            logger.warning("Vendor %r rate-limited for %s; trying next vendor.", vendor, method)
            continue
        except VendorNotConfiguredError as e:
            logger.warning("Vendor %r not configured for %s; trying next vendor.", vendor, method)
            if first_error is None:
                first_error = e  # Surface it if no other vendor can serve the call.
            continue
        except NoMarketDataError as e:
            last_no_data = e  # No data here; another configured vendor may have it
            continue
        except Exception as e:
            # Don't let one vendor's failure crash the call when another can
            # serve it, but never swallow silently: a broken primary must be
            # visible in the logs (#989), not hidden behind a fallback's verdict.
            logger.warning("Vendor %r failed for %s: %s", vendor, method, e)
            if first_error is None:
                first_error = e
            continue

    # If any vendor reported "no data", the symbol is genuinely unavailable.
    # Return one explicit, instructive sentinel rather than a vendor-specific
    # empty string, so the agent reports "unavailable" instead of inventing a
    # value. This takes precedence over incidental fallback errors.
    if last_no_data is not None:
        if first_error is not None:
            # A vendor also hit a real error; surface it in logs so the no-data
            # verdict can't hide a broken primary (network/auth/etc.).
            logger.warning(
                "Returning NO_DATA for %s, but a vendor errored earlier: %s",
                method, first_error,
            )
        sym = last_no_data.symbol
        canonical = last_no_data.canonical
        resolved = "" if canonical == sym else f" (resolved to '{canonical}')"
        # Surface the typed error's detail (e.g. "latest row is 2025-06-11 ...
        # stale") so the agent sees the specific reason — invalid symbol, no
        # coverage, or stale data — not just a generic "unavailable".
        reason = f" ({last_no_data.detail})" if last_no_data.detail else ""
        return (
            f"NO_DATA_AVAILABLE: No usable market data for '{sym}'{resolved} from "
            f"any configured vendor{reason}. The symbol may be invalid, delisted, "
            f"not covered, or the vendor returned stale data. Do not estimate or "
            f"fabricate values — report that data is unavailable for this symbol."
        )

    # No vendor returned data and none reported clean "no data" — surface the
    # first real error (e.g. the primary vendor's network failure).
    if first_error is not None:
        raise first_error

    raise RuntimeError(f"No available vendor for '{method}'")
