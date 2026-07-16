"""AKShare news endpoints: per-stock news + macro/global news."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

import pandas as pd

from .akshare_common import ak_lazy_import, akshare_call, df_to_csv_report
from .config import get_config
from .symbol_utils import normalize_cn_display, normalize_for_akshare


def get_news(
    ticker: Annotated[str, "ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd"],
    end_date: Annotated[str, "End date in yyyy-mm-dd"],
) -> str:
    """Per-stock news via ``stock_news_em`` (eastmoney)."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)

    try:
        raw = akshare_call(ak.stock_news_em, symbol=code)
    except Exception as exc:
        return f"Failed to fetch A-share news for {ticker}: {exc}"

    if raw is None or raw.empty:
        return f"No news found for A-share {ticker}"

    # eastmoney returns columns like:
    #   关键词 / 新闻标题 / 新闻内容 / 发布时间 / 文章来源 / 新闻链接
    col_time = next((c for c in raw.columns if "时间" in c or "日期" in c), None)
    col_title = next((c for c in raw.columns if "标题" in c), None)
    col_body = next((c for c in raw.columns if "内容" in c or "正文" in c), None)
    col_source = next((c for c in raw.columns if "来源" in c), None)
    col_url = next((c for c in raw.columns if "链接" in c or "url" in c.lower()), None)

    if col_time:
        raw[col_time] = pd.to_datetime(raw[col_time], errors="coerce")
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date) + timedelta(days=1)
        raw = raw[(raw[col_time] >= start_dt) & (raw[col_time] < end_dt)]

    if raw.empty:
        return f"No news found for A-share {ticker} between {start_date} and {end_date}"

    display = normalize_cn_display(ticker)
    config = get_config()
    body_items = max(0, int(config.get("news_body_snippet_items", 0) or 0))
    body_chars = max(0, int(config.get("news_body_snippet_chars", 0) or 0))
    lines = [f"# A-share news for {display} ({start_date} -> {end_date})",
             "# Source: AKShare stock_news_em",
             f"# Items: {len(raw)}", ""]
    for idx, (_, row) in enumerate(raw.iterrows()):
        t = row[col_time].strftime("%Y-%m-%d %H:%M") if col_time and pd.notna(row[col_time]) else ""
        title = str(row[col_title]).strip() if col_title else ""
        src = str(row[col_source]).strip() if col_source else ""
        url = str(row[col_url]).strip() if col_url else ""
        lines.append(f"- [{t}] {title}  ({src})")
        if col_body and idx < body_items and body_chars > 0:
            body = _clean_snippet(row[col_body], body_chars)
            if body:
                lines.append(f"  摘录: {body}")
        if url:
            lines.append(f"  {url}")
    return "\n".join(lines)


def _clean_snippet(value: object, max_chars: int) -> str:
    """Normalize a vendor article body and cap it for prompt safety."""
    if value is None or pd.isna(value):
        return ""
    text = " ".join(str(value).split())
    if not text or text.lower() == "nan":
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def get_global_news(
    curr_date: Annotated[str, "current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "days to look back"] = 7,
    limit: Annotated[int, "max items"] = 20,
) -> str:
    """CN macro news: CCTV evening news + Baidu economic calendar."""
    ak = ak_lazy_import()
    sections: list[str] = []

    # CCTV news: news_cctv(date=YYYYMMDD) returns that day's scripts.
    # Walk back `look_back_days` and concatenate.
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d").date()
    cctv_rows: list[pd.DataFrame] = []
    for i in range(look_back_days):
        day = end_dt - timedelta(days=i)
        try:
            df = akshare_call(ak.news_cctv, date=day.strftime("%Y%m%d"))
            if df is not None and not df.empty:
                df = df.head(max(1, limit // max(1, look_back_days)))
                df.insert(0, "date", day.isoformat())
                cctv_rows.append(df)
        except Exception:
            continue

    if cctv_rows:
        cctv_df = pd.concat(cctv_rows, ignore_index=True)
        sections.append(df_to_csv_report(
            cctv_df,
            title=f"CCTV News ({end_dt - timedelta(days=look_back_days - 1)} -> {end_dt})",
        ))

    # Baidu economic calendar (if available)
    try:
        cal = akshare_call(ak.news_economic_baidu, date=end_dt.strftime("%Y%m%d"))
        if cal is not None and not cal.empty:
            sections.append(df_to_csv_report(
                cal.head(limit),
                title=f"Economic calendar around {curr_date}",
            ))
    except Exception:
        pass

    if not sections:
        return f"No macro news available around {curr_date}"
    return "\n\n".join(sections)


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """A-share 'insider' proxy: 龙虎榜 (top-trader board) + 大股东增减持."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)
    display = normalize_cn_display(ticker)
    sections: list[str] = []

    try:
        lhb = akshare_call(ak.stock_lhb_stock_detail_em, symbol=code)
        if lhb is not None and not lhb.empty:
            sections.append(df_to_csv_report(
                lhb.head(50),
                title=f"龙虎榜 (Top-Trader Board) for {display}",
            ))
    except Exception:
        pass

    try:
        holder = akshare_call(ak.stock_share_hold_change_bse, symbol=code)
        if holder is not None and not holder.empty:
            sections.append(df_to_csv_report(
                holder.head(50),
                title=f"Shareholder changes for {display}",
            ))
    except Exception:
        # Try Shenzhen endpoint as a fallback
        try:
            holder = akshare_call(ak.stock_share_hold_change_szse, symbol=code)
            if holder is not None and not holder.empty:
                sections.append(df_to_csv_report(
                    holder.head(50),
                    title=f"Shareholder changes (SZSE) for {display}",
                ))
        except Exception:
            pass

    if not sections:
        return f"No insider-equivalent data available for A-share {display}"
    return "\n\n".join(sections)
