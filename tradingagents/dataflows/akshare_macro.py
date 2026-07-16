"""CN macro / economic calendar data.

First version covers 6 headline series: CPI, PPI, PMI (manufacturing),
M2, LPR (央行贷款市场报价利率), and SHIBOR overnight. Each helper is
cheap enough to fetch on every agent call thanks to the rate limiter.
"""

from __future__ import annotations

from typing import Annotated

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
