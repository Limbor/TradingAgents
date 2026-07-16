"""Company announcements (A-share)."""

from __future__ import annotations

from typing import Annotated

from .akshare_common import ak_lazy_import, akshare_call, df_to_csv_report
from .symbol_utils import normalize_cn_display, normalize_for_akshare


def get_announcements(
    ticker: Annotated[str, "A-share ticker symbol"],
    start_date: Annotated[str, "Start date yyyy-mm-dd"],
    end_date: Annotated[str, "End date yyyy-mm-dd"],
) -> str:
    """Official filings via ``stock_notice_report`` (eastmoney 巨潮)."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)

    try:
        raw = akshare_call(
            ak.stock_individual_notice_report,
            security=code,
            symbol="全部",
            begin_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        return f"Failed to fetch announcements for {display}: {exc}"

    if raw is None or raw.empty:
        return f"No announcements found for A-share {display} in the given period"

    return df_to_csv_report(
        raw.head(80),
        title=f"Announcements for {display} ({start_date} -> {end_date})",
    )
