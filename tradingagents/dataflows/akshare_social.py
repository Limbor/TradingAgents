"""Social sentiment endpoints (A-share only).

First version: popularity metrics + title snapshots from eastmoney
popularity ranking and Xueqiu (雪球) follow tracker. Body-text
ingestion will come in a later iteration.
"""

from __future__ import annotations

from typing import Annotated

import pandas as pd

from .akshare_common import akshare_call, ak_lazy_import, df_to_csv_report
from .akshare_cn_specific import _append_hot_rank_fallback
from .symbol_utils import normalize_for_akshare, normalize_cn_display


def get_social_sentiment(
    ticker: Annotated[str, "A-share ticker symbol"],
) -> str:
    """Popularity / attention metrics for an A-share."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)
    sections: list[str] = []

    # Eastmoney hot-stock ranking (pan-market; filter to our ticker)
    try:
        hot = akshare_call(ak.stock_hot_rank_em)
        if hot is None or hot.empty:
            _append_hot_rank_fallback(sections, code, display, "AKShare wrapper returned no rank data.")
        else:
            col_code = next((c for c in hot.columns if "代码" in c), None)
            if col_code is not None:
                row = hot[hot[col_code].astype(str).str.contains(code, na=False)]
                if not row.empty:
                    sections.append(df_to_csv_report(
                        row,
                        title=f"Eastmoney hot rank snapshot for {display}",
                    ))
                else:
                    sections.append(f"# {display} not in current eastmoney hot ranking top list.")
    except Exception as exc:
        _append_hot_rank_fallback(sections, code, display, f"AKShare wrapper unavailable: {exc}")

    # Xueqiu follow tracker
    try:
        xq = akshare_call(ak.stock_hot_follow_xq, symbol="最热门")
        if xq is not None and not xq.empty:
            col_code = next((c for c in xq.columns if "代码" in c or "symbol" in c.lower()), None)
            if col_code is not None:
                match = xq[xq[col_code].astype(str).str.contains(code, na=False)]
                if not match.empty:
                    sections.append(df_to_csv_report(
                        match,
                        title=f"Xueqiu follow tracker for {display}",
                    ))
    except Exception as exc:
        sections.append(f"# (xueqiu follow tracker unavailable: {exc})")

    # Baidu hot index (broader attention metric)
    try:
        baidu = akshare_call(ak.stock_zh_a_alerts_cls)
        if baidu is not None and not baidu.empty:
            sample = baidu.head(15)
            sections.append(df_to_csv_report(
                sample,
                title="Recent CLS market alerts (market-wide)",
            ))
    except Exception:
        pass

    if not sections:
        return f"No social sentiment data currently available for A-share {display}"
    return "\n\n".join(sections)
