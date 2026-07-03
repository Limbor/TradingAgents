"""A-share only market microstructure data.

- Limit-up / limit-down status & ST flags
- Northbound (Stock Connect) flow
- Margin trading / short balance
- Upcoming share unlock (解禁) schedule
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Annotated

import pandas as pd

from .akshare_common import akshare_call, ak_lazy_import, df_to_csv_report
from .symbol_utils import normalize_for_akshare, normalize_cn_display

logger = logging.getLogger(__name__)


def _find_col(df: pd.DataFrame, *needles: str) -> str | None:
    for col in df.columns:
        lowered = str(col).lower()
        if any(needle in str(col) or needle.lower() in lowered for needle in needles):
            return col
    return None


def _filter_code(df: pd.DataFrame, code: str) -> pd.DataFrame:
    col_code = _find_col(df, "代码", "symbol")
    if col_code is None:
        return df
    return df[df[col_code].astype(str).str.contains(code, na=False)].copy()


def _daily_limit_pct(display: str, is_st: bool) -> float:
    code, _, exchange = display.partition(".")
    if is_st:
        return 5.0
    if exchange == "BJ":
        return 30.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0


def _fmt_float(value, digits: int = 2) -> str:
    try:
        if pd.isna(value):
            return "N/A"
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def get_market_structure_snapshot(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "date yyyy-mm-dd"],
) -> str:
    """A-share trading-state snapshot: limits, ST flag, liquidity, and tradability."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)
    date_cmp = curr_date.replace("-", "")
    sections: list[str] = []
    is_st = False

    try:
        st = akshare_call(ak.stock_zh_a_st_em)
        st_row = _filter_code(st, code) if st is not None and not st.empty else pd.DataFrame()
        is_st = not st_row.empty
    except Exception as exc:
        sections.append(f"# ST flag unavailable: {exc}")

    limit_pct = _daily_limit_pct(display, is_st)
    try:
        hist = akshare_call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=(pd.to_datetime(curr_date) - pd.Timedelta(days=15)).strftime("%Y%m%d"),
            end_date=date_cmp,
            adjust="qfq",
        )
        if hist is None or hist.empty:
            sections.append(f"# No recent OHLCV returned for {display} up to {curr_date}")
        else:
            hist = hist.rename(
                columns={
                    "日期": "Date",
                    "开盘": "Open",
                    "收盘": "Close",
                    "最高": "High",
                    "最低": "Low",
                    "成交量": "Volume",
                    "成交额": "Amount",
                    "涨跌幅": "PctChange",
                    "换手率": "Turnover",
                    "振幅": "Amplitude",
                }
            )
            hist["Date"] = pd.to_datetime(hist["Date"], errors="coerce")
            hist = hist.dropna(subset=["Date"]).sort_values("Date")
            latest = hist.iloc[-1]
            previous = hist.iloc[-2] if len(hist) >= 2 else latest
            prev_close = float(previous.get("Close", latest.get("Close")))
            up_price = round(prev_close * (1 + limit_pct / 100), 2)
            down_price = round(prev_close * (1 - limit_pct / 100), 2)
            close = float(latest.get("Close"))
            pct_change = float(latest.get("PctChange", 0))
            if pct_change >= limit_pct - 0.05 or close >= up_price - 0.01:
                limit_state = "LIMIT_UP_OR_NEAR_LIMIT_UP"
            elif pct_change <= -limit_pct + 0.05 or close <= down_price + 0.01:
                limit_state = "LIMIT_DOWN_OR_NEAR_LIMIT_DOWN"
            else:
                limit_state = "NORMAL_TRADING_RANGE"
            summary = pd.DataFrame(
                [
                    {
                        "ticker": display,
                        "latest_trading_date": latest["Date"].strftime("%Y-%m-%d"),
                        "is_st": is_st,
                        "daily_limit_pct": limit_pct,
                        "prev_close": _fmt_float(prev_close),
                        "limit_up_price_est": _fmt_float(up_price),
                        "limit_down_price_est": _fmt_float(down_price),
                        "close": _fmt_float(close),
                        "pct_change": _fmt_float(pct_change),
                        "turnover_pct": _fmt_float(latest.get("Turnover")),
                        "amount_cny": _fmt_float(latest.get("Amount"), 0),
                        "amplitude_pct": _fmt_float(latest.get("Amplitude")),
                        "limit_state": limit_state,
                        "t_plus_1_note": "A-shares are T+1; shares bought today cannot be sold today.",
                    }
                ]
            )
            sections.append(
                df_to_csv_report(
                    summary,
                    title=f"A-share market-structure snapshot for {display} as of {curr_date}",
                )
            )
    except Exception as exc:
        sections.append(f"# Recent OHLCV / limit-price calculation unavailable: {exc}")

    for title, fn_name in (
        ("Limit-up pool row", "stock_zt_pool_em"),
        ("Limit-down pool row", "stock_zt_pool_dtgc_em"),
    ):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            pool = akshare_call(fn, date=date_cmp)
            row = _filter_code(pool, code) if pool is not None and not pool.empty else pd.DataFrame()
            if not row.empty:
                sections.append(df_to_csv_report(row, title=f"{title}: {display} on {curr_date}"))
        except Exception as exc:
            sections.append(f"# {title} unavailable: {exc}")

    if not sections:
        return f"No A-share market-structure data available for {display} on {curr_date}"
    return "\n\n".join(sections)


def get_theme_heat(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "date yyyy-mm-dd"],
    top_n: Annotated[int, "number of leading themes/industries to return"] = 20,
) -> str:
    """Market-wide theme and industry heat relevant to A-share momentum."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)
    sections: list[str] = []

    try:
        hot = akshare_call(ak.stock_hot_rank_em)
        row = _filter_code(hot, code) if hot is not None and not hot.empty else pd.DataFrame()
        if not row.empty:
            sections.append(df_to_csv_report(row, title=f"Retail heat rank for {display}"))
    except Exception as exc:
        sections.append(f"# Retail heat rank unavailable: {exc}")

    for title, fn_name in (
        ("Top concept boards", "stock_board_concept_name_em"),
        ("Top industry boards", "stock_board_industry_name_em"),
    ):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            sections.append(f"# {title}: akshare.{fn_name} not available")
            continue
        try:
            board = akshare_call(fn)
            if board is None or board.empty:
                sections.append(f"# {title}: no data returned")
                continue
            sections.append(df_to_csv_report(board.head(max(1, min(top_n, 50))), title=f"{title} around {curr_date}"))
        except Exception as exc:
            sections.append(f"# {title} unavailable: {exc}")

    return "\n\n".join(sections) if sections else f"No theme heat data available for {display}"


def get_lhb_detail(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "date yyyy-mm-dd"],
    look_back_days: Annotated[int, "days to look back"] = 30,
) -> str:
    """Structured Dragon-Tiger List (龙虎榜) detail and event context."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)
    sections: list[str] = []

    try:
        detail = akshare_call(ak.stock_lhb_stock_detail_em, symbol=code)
        if detail is not None and not detail.empty:
            date_col = _find_col(detail, "日期", "上榜日", "time")
            if date_col:
                detail[date_col] = pd.to_datetime(detail[date_col], errors="coerce")
                end_dt = pd.to_datetime(curr_date)
                start_dt = end_dt - pd.Timedelta(days=look_back_days)
                detail = detail[(detail[date_col] >= start_dt) & (detail[date_col] <= end_dt)]
                detail = detail.sort_values(date_col, ascending=False)
            sections.append(
                df_to_csv_report(
                    detail.head(30),
                    title=f"Dragon-Tiger List detail for {display} ({look_back_days}d ending {curr_date})",
                )
            )
        else:
            sections.append(f"# No Dragon-Tiger List detail returned for {display}")
    except Exception as exc:
        sections.append(f"# Dragon-Tiger List detail unavailable: {exc}")

    try:
        yyb = getattr(ak, "stock_lhb_yyb_detail_em", None)
        if yyb is not None:
            broker = akshare_call(yyb, symbol=code)
            if broker is not None and not broker.empty:
                sections.append(df_to_csv_report(broker.head(30), title=f"Broker-seat detail for {display}"))
    except Exception as exc:
        sections.append(f"# Broker-seat detail unavailable: {exc}")

    return "\n\n".join(sections)


def get_limit_status(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "date yyyy-mm-dd"],
) -> str:
    """Return limit-up/down membership and ST flag for given date."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)
    date_cmp = curr_date.replace("-", "")
    sections: list[str] = []

    # Limit-up pool
    try:
        zt = akshare_call(ak.stock_zt_pool_em, date=date_cmp)
        if zt is not None and not zt.empty:
            col_code = next((c for c in zt.columns if "代码" in c), None)
            if col_code is not None:
                row = zt[zt[col_code].astype(str) == code]
                if not row.empty:
                    sections.append(df_to_csv_report(
                        row, title=f"{display} was on the LIMIT-UP pool on {curr_date}"))
                else:
                    sections.append(f"# {display}: not on limit-up pool on {curr_date}")
    except Exception as exc:
        sections.append(f"# (limit-up pool unavailable: {exc})")

    # ST flag
    try:
        st = akshare_call(ak.stock_zh_a_st_em)
        if st is not None and not st.empty:
            col_code = next((c for c in st.columns if "代码" in c), None)
            if col_code is not None:
                flag = st[st[col_code].astype(str) == code]
                if not flag.empty:
                    sections.append(df_to_csv_report(flag, title=f"{display} has ST status"))
                else:
                    sections.append(f"# {display}: no ST flag today")
    except Exception as exc:
        logger.warning("Failed to check ST status for %s: %s", display, exc)
        sections.append(f"# {display}: ST status unavailable ({exc})")

    return "\n\n".join(sections) if sections else f"No limit/ST data for {display} on {curr_date}"


def get_northbound_flow(
    curr_date: Annotated[str, "date yyyy-mm-dd"],
    look_back_days: Annotated[int, "days to look back"] = 30,
) -> str:
    """Stock Connect (沪深港通) net flow history."""
    ak = ak_lazy_import()
    try:
        df = akshare_call(ak.stock_hsgt_hist_em, symbol="北向资金")
    except Exception as exc:
        return f"Failed to fetch northbound flow: {exc}"

    if df is None or df.empty:
        return "No northbound flow data returned."

    col_time = next((c for c in df.columns if "日期" in c or "时间" in c), None)
    if col_time:
        df[col_time] = pd.to_datetime(df[col_time], errors="coerce")
        end_dt = pd.to_datetime(curr_date)
        start_dt = end_dt - timedelta(days=look_back_days)
        df = df[(df[col_time] >= start_dt) & (df[col_time] <= end_dt)]

    return df_to_csv_report(df.tail(look_back_days),
                            title=f"Northbound flow ({look_back_days}d ending {curr_date})")


def get_margin_balance(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "date yyyy-mm-dd"],
) -> str:
    """Margin trading balance + short balance for given ticker."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)

    try:
        df = akshare_call(ak.stock_margin_detail_szse, date=curr_date.replace("-", ""))
    except Exception:
        df = None
    if df is None or df.empty:
        try:
            df = akshare_call(ak.stock_margin_detail_sse, date=curr_date.replace("-", ""))
        except Exception as exc:
            return f"Failed to fetch margin balance for {display}: {exc}"

    if df is None or df.empty:
        return f"No margin balance data for {display} on {curr_date}"

    col_code = next((c for c in df.columns if "代码" in c), None)
    if col_code is not None:
        df = df[df[col_code].astype(str) == code]

    if df.empty:
        return f"{display} has no margin trading record on {curr_date}"
    return df_to_csv_report(df, title=f"Margin balance for {display} on {curr_date}")


def get_unlock_schedule(
    ticker: Annotated[str, "A-share ticker"],
    curr_date: Annotated[str, "date yyyy-mm-dd"],
) -> str:
    """Upcoming share unlock schedule (解禁)."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)

    try:
        df = akshare_call(ak.stock_restricted_release_queue_em)
    except Exception as exc:
        return f"Failed to fetch unlock schedule: {exc}"

    if df is None or df.empty:
        return f"No unlock schedule data for {display}"

    col_code = next((c for c in df.columns if "代码" in c), None)
    col_time = next((c for c in df.columns if "日期" in c), None)
    if col_code is not None:
        df = df[df[col_code].astype(str) == code]
    if df.empty:
        return f"No upcoming unlock events for {display}"
    if col_time is not None:
        df[col_time] = pd.to_datetime(df[col_time], errors="coerce")
        df = df.sort_values(col_time)

    return df_to_csv_report(df.head(20),
                            title=f"Upcoming unlocks for {display} (as of {curr_date})")
