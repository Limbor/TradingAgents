"""AKShare: A-share OHLCV + technical indicators.

Caches raw OHLCV to CSV under ``data_cache_dir`` using the same layout
as :mod:`stockstats_utils.load_ohlcv`, but swaps the downloader for
``akshare.stock_zh_a_hist`` with forward adjustment (``qfq``). Column
names are normalized to ``Date/Open/High/Low/Close/Volume`` so the
existing ``_clean_dataframe`` and ``stockstats.wrap`` pipelines work
without modification.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

import pandas as pd
from stockstats import wrap

from .akshare_common import akshare_call, ak_lazy_import
from .config import get_config
from .stockstats_utils import _clean_dataframe
from .symbol_utils import normalize_for_akshare, normalize_cn_display


# ---------------------------------------------------------------------------
# Column mapping from AKShare (Chinese) to framework canonical (English).
# AKShare's stock_zh_a_hist returns these columns:
#   日期 / 股票代码 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额 / 振幅 / ...
# ---------------------------------------------------------------------------
_AK_COL_MAP = {
    "日期": "Date",
    "开盘": "Open",
    "收盘": "Close",
    "最高": "High",
    "最低": "Low",
    "成交量": "Volume",
    "成交额": "Amount",
    "涨跌幅": "PctChange",
    "涨跌额": "Change",
    "换手率": "Turnover",
}


def _download_ak_ohlcv(code: str, start: str, end: str) -> pd.DataFrame:
    ak = ak_lazy_import()
    raw = akshare_call(
        ak.stock_zh_a_hist,
        symbol=code,
        period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust="qfq",
    )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

    df = raw.rename(columns=_AK_COL_MAP)
    # keep only the columns we need, in canonical order
    keep = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume", "Amount", "Turnover") if c in df.columns]
    return df[keep]


def load_ohlcv_cn(symbol: str, curr_date: str) -> pd.DataFrame:
    """A-share counterpart of :func:`stockstats_utils.load_ohlcv`.

    Caches a 5-year window per symbol to avoid repeated downloads, then
    filters to ``curr_date`` to prevent look-ahead bias.
    """
    code = normalize_for_akshare(symbol)
    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{code}-AKShare-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    else:
        data = _download_ak_ohlcv(code, start_str, end_str)
        data.to_csv(data_file, index=False, encoding="utf-8")

    data = _clean_dataframe(data)
    data = data[data["Date"] <= curr_date_dt]
    return data


def get_stock(
    symbol: Annotated[str, "ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd"],
    end_date: Annotated[str, "End date in yyyy-mm-dd"],
) -> str:
    """OHLCV report for A-share between ``start_date`` and ``end_date``."""
    data = load_ohlcv_cn(symbol, end_date)
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)
    windowed = data[(data["Date"] >= start_dt) & (data["Date"] <= end_dt)].copy()

    if windowed.empty:
        return f"No A-share data found for '{symbol}' between {start_date} and {end_date}"

    numeric_cols = [c for c in ("Open", "High", "Low", "Close") if c in windowed.columns]
    for col in numeric_cols:
        windowed[col] = windowed[col].round(2)

    display = normalize_cn_display(symbol)
    header = (
        f"# A-share OHLCV for {display} from {start_date} to {end_date}\n"
        f"# Total records: {len(windowed)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"# Price unit: CNY, volume unit: shares (股)\n\n"
    )
    return header + windowed.to_csv(index=False)


# ---------------------------------------------------------------------------
# Technical indicators - computed locally via stockstats from AK-provided OHLCV.
# ---------------------------------------------------------------------------

def _indicator_description(indicator: str) -> str:
    # Same descriptions used in y_finance.get_stock_stats_indicators_window.
    # Imported lazily to avoid a circular import at module load time.
    from .y_finance import get_stock_stats_indicators_window  # noqa: F401
    return ""


def get_indicator(
    symbol: Annotated[str, "ticker symbol"],
    indicator: Annotated[str, "technical indicator name (e.g. rsi, macd)"],
    curr_date: Annotated[str, "current trading date, yyyy-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """Compute a technical indicator on A-share OHLCV and format a report.

    Non-trading days are skipped using the CN trading calendar;
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

    data = load_ohlcv_cn(symbol, curr_date)
    if data.empty:
        return f"No A-share data available for {symbol} up to {curr_date}"

    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    try:
        df[indicator]  # triggers stockstats to compute in place
    except Exception as exc:
        return f"Failed to compute indicator '{indicator}' for {symbol}: {exc}"

    index = {row["Date"]: row[indicator] for _, row in df.iterrows()}

    curr_dt = pd.to_datetime(curr_date).date()
    start = curr_dt - timedelta(days=look_back_days)
    trade_days = trading_days_between(start, curr_dt)

    lines = []
    for day in reversed(trade_days):  # newest first
        key = day.strftime("%Y-%m-%d")
        value = index.get(key)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            value = "N/A"
        lines.append(f"{key}: {value}")

    display = normalize_cn_display(symbol)
    header = (
        f"## {indicator} values for A-share {display} "
        f"from {start.strftime('%Y-%m-%d')} to {curr_date}\n"
        f"(skipping non-trading days; {len(trade_days)} trading days shown)\n\n"
    )
    return header + "\n".join(lines)
