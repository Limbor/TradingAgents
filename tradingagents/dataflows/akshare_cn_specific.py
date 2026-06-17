"""A-share only market microstructure data.

- Limit-up / limit-down status & ST flags
- Northbound (Stock Connect) flow
- Margin trading / short balance
- Upcoming share unlock (解禁) schedule
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

import pandas as pd

from .akshare_common import akshare_call, ak_lazy_import, df_to_csv_report
from .symbol_utils import normalize_for_akshare, normalize_cn_display


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
    except Exception:
        pass

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
