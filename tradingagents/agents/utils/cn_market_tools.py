"""A-share specific tools exposed to analyst LLMs.

All tools route through :func:`tradingagents.dataflows.interface.route_to_vendor`
so market-aware vendor selection (AKShare / TuShare) and rate limiting are
honored consistently.
"""

from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor


# -------------------- social sentiment --------------------

@tool
def get_social_sentiment(
    ticker: Annotated[str, "A-share ticker (6-digit code, optionally .SH/.SZ/.BJ)"]
) -> str:
    """Retrieve retail-investor sentiment / popularity metrics for an A-share.

    First iteration returns popularity rank (eastmoney), Xueqiu (雪球) follow
    tracker snapshot, and recent market-wide CLS alerts. Full post body
    ingestion will come in a later iteration.

    Args:
        ticker (str): A-share ticker such as "600519" or "600519.SH".
    Returns:
        str: A formatted social-sentiment snapshot report.
    """
    return route_to_vendor("get_social_sentiment", ticker)


# -------------------- announcements --------------------

@tool
def get_announcements(
    ticker: Annotated[str, "A-share ticker (6-digit code, optionally .SH/.SZ/.BJ)"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Retrieve official company filings (公告) for an A-share between dates.

    Filings typically include earnings releases, shareholder changes,
    guarantees, reorganizations, etc. Sourced from eastmoney / 巨潮.
    """
    return route_to_vendor("get_announcements", ticker, start_date, end_date)


# -------------------- macro --------------------

@tool
def get_macro_calendar(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Trailing window in days"] = 90,
) -> str:
    """Compact CN macro snapshot: CPI / PPI / PMI / M2 / LPR / SHIBOR O/N.

    Returns the most recent readings per series; frequency varies by
    indicator (monthly CPI/PPI/PMI/M2, quarterly-ish LPR, daily SHIBOR).
    """
    return route_to_vendor("get_macro_calendar", curr_date, look_back_days)


# -------------------- A-share microstructure --------------------

@tool
def get_limit_status(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "Date in yyyy-mm-dd"],
) -> str:
    """Return limit-up / limit-down pool membership and ST flag on a given date.

    Highly relevant for A-share risk management: an ST-flagged stock faces
    ±5% daily limits instead of ±10%, and limit-up/down events often cluster.
    """
    return route_to_vendor("get_limit_status", ticker, curr_date)


@tool
def get_northbound_flow(
    curr_date: Annotated[str, "Date in yyyy-mm-dd"],
    look_back_days: Annotated[int, "Days to look back"] = 30,
) -> str:
    """Stock Connect (沪深港通) northbound net flow history.

    Northbound flow is widely treated as a 'smart money' indicator for
    A-shares; sustained inflows/outflows often precede market rotation.
    """
    return route_to_vendor("get_northbound_flow", curr_date, look_back_days)


@tool
def get_margin_balance(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "Date in yyyy-mm-dd"],
) -> str:
    """Margin trading balance & short balance (融资融券) for a stock on a given date."""
    return route_to_vendor("get_margin_balance", ticker, curr_date)


@tool
def get_unlock_schedule(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "Date in yyyy-mm-dd"],
) -> str:
    """Upcoming restricted-share unlock (解禁) schedule for a stock."""
    return route_to_vendor("get_unlock_schedule", ticker, curr_date)
