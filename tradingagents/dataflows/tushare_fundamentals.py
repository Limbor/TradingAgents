"""TuShare: fundamentals (financial statements + valuation metrics)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import pandas as pd

from .akshare_common import df_to_csv_report  # shared CSV rendering helper
from .symbol_utils import normalize_for_tushare, normalize_cn_display
from .tushare_common import get_pro_api, tushare_call


def _year_bounds(curr_date: str | None) -> tuple[str, str]:
    if not curr_date:
        today = datetime.today()
    else:
        today = datetime.strptime(curr_date, "%Y-%m-%d")
    start = today.replace(year=today.year - 3)
    return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")


def _period_param(freq: str, curr_date: str | None) -> dict:
    """Build `start_date/end_date` and inferred report periods for TuShare."""
    start, end = _year_bounds(curr_date)
    return {"start_date": start, "end_date": end}


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "date yyyy-mm-dd (for valuation metrics)"] = None,
) -> str:
    """Company overview + latest valuation metrics via ``daily_basic``."""
    pro = get_pro_api()
    ts_code = normalize_for_tushare(ticker)
    display = normalize_cn_display(ticker)

    overview_lines = [f"# A-share fundamentals for {display}"]

    # company overview
    try:
        company = tushare_call(
            pro.stock_company,
            ts_code=ts_code,
            fields="ts_code,chairman,manager,reg_capital,setup_date,province,city,introduction,employees,main_business,business_scope",
        )
        if company is not None and not company.empty:
            row = company.iloc[0]
            for col in company.columns:
                val = row[col]
                if pd.notna(val) and str(val).strip():
                    overview_lines.append(f"- **{col}**: {val}")
    except Exception as exc:
        overview_lines.append(f"- (stock_company failed: {exc})")

    # latest daily_basic valuation snapshot
    try:
        trade_date = curr_date.replace("-", "") if curr_date else None
        daily = tushare_call(
            pro.daily_basic,
            ts_code=ts_code,
            trade_date=trade_date,
            fields="ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv",
        )
        if (daily is None or daily.empty) and trade_date:
            # fall back to most recent available trade_date
            daily = tushare_call(
                pro.daily_basic,
                ts_code=ts_code,
                fields="ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv",
            )
            if daily is not None and not daily.empty:
                daily = daily.sort_values("trade_date").tail(1)
        if daily is not None and not daily.empty:
            overview_lines.append("")
            overview_lines.append("## Valuation snapshot (daily_basic)")
            row = daily.iloc[0]
            for col in daily.columns:
                val = row[col]
                if pd.notna(val):
                    overview_lines.append(f"- {col}: {val}")
    except Exception as exc:
        overview_lines.append(f"- (daily_basic failed: {exc})")

    return "\n".join(overview_lines)


def _fetch_statement(endpoint, ts_code: str, freq: str, curr_date: str | None) -> pd.DataFrame:
    pro = get_pro_api()
    fn = getattr(pro, endpoint)
    params = _period_param(freq, curr_date)
    report_type = 1  # consolidated
    df = tushare_call(fn, ts_code=ts_code, **params, report_type=report_type)
    if df is None or df.empty:
        return pd.DataFrame()
    if freq.lower() == "annual":
        df = df[df["end_date"].astype(str).str.endswith("1231")]
    df = df.sort_values("end_date", ascending=False)
    return df.head(8)  # last 8 periods


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "annual / quarterly"] = "quarterly",
    curr_date: Annotated[str, "date yyyy-mm-dd"] = None,
) -> str:
    ts_code = normalize_for_tushare(ticker)
    df = _fetch_statement("balancesheet", ts_code, freq, curr_date)
    return df_to_csv_report(
        df, title=f"Balance sheet ({freq}) for {normalize_cn_display(ticker)}"
    )


def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "annual / quarterly"] = "quarterly",
    curr_date: Annotated[str, "date yyyy-mm-dd"] = None,
) -> str:
    ts_code = normalize_for_tushare(ticker)
    df = _fetch_statement("cashflow", ts_code, freq, curr_date)
    return df_to_csv_report(
        df, title=f"Cash flow ({freq}) for {normalize_cn_display(ticker)}"
    )


def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "annual / quarterly"] = "quarterly",
    curr_date: Annotated[str, "date yyyy-mm-dd"] = None,
) -> str:
    ts_code = normalize_for_tushare(ticker)
    df = _fetch_statement("income", ts_code, freq, curr_date)
    return df_to_csv_report(
        df, title=f"Income statement ({freq}) for {normalize_cn_display(ticker)}"
    )
