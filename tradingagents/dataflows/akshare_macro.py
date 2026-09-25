"""CN macro / economic calendar data.

First version covers 6 headline series: CPI, PPI, PMI (manufacturing),
M2, LPR (央行贷款市场报价利率), and SHIBOR overnight. Each helper is
cheap enough to fetch on every agent call thanks to the rate limiter.
"""

from __future__ import annotations

from typing import Annotated, Any

import pandas as pd

from .akshare_common import ak_lazy_import, akshare_call, df_to_csv_report


def _tail(df: pd.DataFrame | None, n: int) -> pd.DataFrame | None:
    if df is None or df.empty:
        return df
    return df.tail(n)


def get_macro_calendar(
    curr_date: Annotated[str, "current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "trailing window in days"] = 90,
) -> str:
    """Compact CN macro snapshot.

    Returns the most recent readings for CPI, PPI, PMI, M2, LPR, and
    SHIBOR O/N. ``look_back_days`` loosely controls how much history is
    surfaced per series (we keep last N rows; AKShare series are monthly
    or sparse, so 90 days ~ last 3 prints for monthlies).
    """
    ak = ak_lazy_import()
    tail_n = max(3, look_back_days // 30 + 2)
    sections: list[str] = []

    series = [
        ("CPI (同比)", "macro_china_cpi_yearly"),
        ("PPI (同比)", "macro_china_ppi_yearly"),
        ("PMI (制造业)", "macro_china_pmi_yearly"),
        ("M2 (货币供应)", "macro_china_m2_yearly"),
        ("LPR 报价利率", "macro_china_lpr"),
        ("SHIBOR 隔夜", "macro_china_shibor_all"),
    ]

    for title, fn_name in series:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            sections.append(f"# {title}: akshare.{fn_name} not available in installed version")
            continue
        try:
            df = akshare_call(fn)
        except Exception as exc:
            sections.append(f"# {title}: fetch failed ({exc})")
            continue
        sections.append(df_to_csv_report(_tail(df, tail_n), title=title))

    return "\n\n".join(sections) if sections else f"No macro data around {curr_date}"


def get_macro_snapshot(
    curr_date: Annotated[str, "current date yyyy-mm-dd"],
) -> list[dict[str, Any]]:
    """Latest reading per headline macro series, as structured items.

    Structured counterpart of :func:`get_macro_calendar` for the
    market_overview skill payload: ``[{"name", "date", "value"}]``.
    Series that fail to fetch are silently skipped (degraded, not fatal).
    """
    ak = ak_lazy_import()
    series = [
        ("CPI 同比", "macro_china_cpi_yearly"),
        ("PPI 同比", "macro_china_ppi_yearly"),
        ("制造业 PMI", "macro_china_pmi_yearly"),
        ("M2 同比", "macro_china_m2_yearly"),
        ("LPR 报价利率", "macro_china_lpr"),
        ("SHIBOR 隔夜", "macro_china_shibor_all"),
    ]
    items: list[dict[str, Any]] = []
    for name, fn_name in series:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            df = akshare_call(fn)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_date = next((c for c in df.columns if "日期" in str(c) or "date" in str(c).lower()), None)
        col_value = next(
            (c for c in df.columns if any(k in str(c) for k in ("今值", "现值", "报告值"))),
            None,
        )
        row = None
        value = None
        if col_value is not None:
            # Calendar-style series append scheduled-but-unpublished rows with
            # a NaN reading; keep the latest row that actually has a value.
            published = df[pd.to_numeric(df[col_value], errors="coerce").notna()]
            if not published.empty:
                row = published.tail(1).iloc[0]
                value = float(row[col_value])
        if row is None:
            # Fall back to the last row and its last numeric cell.
            row = df.tail(1).iloc[0]
            numeric = pd.to_numeric(row, errors="coerce").dropna()
            value = float(numeric.iloc[-1]) if not numeric.empty else None
        date_str = str(row[col_date]).split(" ")[0] if col_date is not None else ""
        items.append({"name": name, "date": date_str, "value": value})
    return items
