"""TuShare: A-share OHLCV + technical indicators (fallback for AKShare).

Used when AKShare's eastmoney API is rate-limited or unavailable.
Reuses the same ``_clean_dataframe`` + ``stockstats.wrap`` pipeline so
indicator computation is identical regardless of data source.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

import pandas as pd
from stockstats import wrap

from .config import get_config
from .stockstats_utils import _clean_dataframe
from .symbol_utils import normalize_for_tushare, normalize_cn_display
from .tushare_common import get_pro_api, tushare_call

# Column mapping: TuShare -> canonical (same as stockstats_utils expects)
_TS_COL_MAP = {
    "trade_date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "vol": "Volume",
}


def _download_ts_ohlcv(code_ts: str, start: str, end: str) -> pd.DataFrame:
    """Download OHLCV via TuShare ``pro_bar`` with forward adjustment.

    Note: ``ts.pro_bar`` is a top-level helper, NOT a method on the
    ``pro_api()`` client. Calling ``pro.pro_bar`` returns the cryptic
    error "请指定正确的接口名". We still go through ``get_pro_api()``
    to ensure the token is set globally before invoking the helper.
    """
    import tushare as ts  # type: ignore

    get_pro_api()  # ensures ts.set_token(...) has been called
    raw = tushare_call(
        ts.pro_bar,
        ts_code=code_ts,
        adj="qfq",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    # TuShare returns rows in reverse-chronological order; sort ascending
    # so stockstats sees the same orientation as AKShare/yfinance produce.
    df = raw.rename(columns=_TS_COL_MAP)
    keep = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep].copy()
    if "Date" in df.columns:
        # Normalize trade_date (e.g. "20260429") to canonical ISO "2026-04-29".
        # Without this, the CSV cache roundtrip turns strings into int64,
        # and pd.to_datetime(20260429) parses ints as NANOSECONDS-since-epoch
        # (-> 1970-01-01), silently corrupting all subsequent date lookups.
        df["Date"] = pd.to_datetime(df["Date"].astype(str), format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
    return df


def load_ohlcv_ts(symbol: str, curr_date: str) -> pd.DataFrame:
    """A-share OHLCV via TuShare with local cache (5-year window).

    Falls back when AKShare's eastmoney API is unavailable.
    """
    code_ts = normalize_for_tushare(symbol)
    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{code_ts}-TS-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    else:
        data = _download_ts_ohlcv(code_ts, start_str, end_str)
        data.to_csv(data_file, index=False, encoding="utf-8")

    data = _clean_dataframe(data)
    data = data[data["Date"] <= curr_date_dt]
    return data


def get_stock(
    symbol: Annotated[str, "ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd"],
    end_date: Annotated[str, "End date in yyyy-mm-dd"],
) -> str:
    """OHLCV report for A-share via TuShare between start_date and end_date."""
    data = load_ohlcv_ts(symbol, end_date)
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    windowed = data[(data["Date"] >= start_dt) & (data["Date"] <= end_dt)].copy()

    if windowed.empty:
        return f"No TuShare A-share data found for '{symbol}' between {start_date} and {end_date}"

    numeric_cols = [c for c in ("Open", "High", "Low", "Close") if c in windowed.columns]
    windowed[numeric_cols] = windowed[numeric_cols].round(2)

    display = normalize_cn_display(symbol)
    header = (
        f"# TuShare OHLCV for {display} from {start_date} to {end_date}\n"
        f"# Total records: {len(windowed)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Price unit: CNY, volume unit: shares (股)\n\n"
    )
    return header + windowed.to_csv(index=False)


def get_indicator(
    symbol: Annotated[str, "ticker symbol"],
    indicator: Annotated[str, "technical indicator name (e.g. rsi, macd)"],
    curr_date: Annotated[str, "current trading date, yyyy-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """Compute a technical indicator from TuShare OHLCV + stockstats.

    Non-trading days are skipped via the CN trading calendar;
    ``curr_date`` is automatically adjusted to the most recent
    trading day so a non-trading input never returns empty.
    """
    from .cn_trading_calendar import (
        prev_trading_day,
        trading_days_between,
    )
    from datetime import timedelta

    # Adjust non-trading-day input to the last actual trading day
    curr_date = prev_trading_day(curr_date).strftime("%Y-%m-%d")

    data = load_ohlcv_ts(symbol, curr_date)
    if data.empty:
        return f"No TuShare data available for {symbol} up to {curr_date}"

    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    try:
        df[indicator]  # triggers stockstats to compute in place
    except Exception as exc:
        return f"Failed to compute indicator '{indicator}' via TuShare for {symbol}: {exc}"

    index = {row["Date"]: row[indicator] for _, row in df.iterrows()}

    curr_dt = pd.to_datetime(curr_date).date()
    start = curr_dt - timedelta(days=look_back_days)
    trade_days = trading_days_between(start, curr_dt)

    lines = []
    for day in reversed(trade_days):
        key = day.strftime("%Y-%m-%d")
        value = index.get(key)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            value = "N/A"
        lines.append(f"{key}: {value}")

    display = normalize_cn_display(symbol)
    header = (
        f"## {indicator} values for A-share {display} (via TuShare) "
        f"from {start.strftime('%Y-%m-%d')} to {curr_date}\n"
        f"(skipping non-trading days; {len(trade_days)} trading days shown)\n\n"
    )
    return header + "\n".join(lines)
