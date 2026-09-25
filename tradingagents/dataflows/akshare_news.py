"""AKShare news endpoints: per-stock news + macro/global news."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Annotated

import pandas as pd

from .akshare_common import ak_lazy_import, akshare_call, df_to_csv_report
from .config import get_config
from .symbol_utils import normalize_cn_display, normalize_for_akshare

logger = logging.getLogger(__name__)

_EM_TAG_RE = re.compile(r"<[^>]+>")


def _em_news_df(items: list[dict]) -> pd.DataFrame | None:
    """Normalize Eastmoney search-API article items into the stock_news_em shape.

    The search API wraps keyword hits in ``<em>`` tags (preTag/postTag), so
    titles/bodies are stripped of markup before they reach the report.
    """
    rows = []
    for item in items:
        title = _EM_TAG_RE.sub("", str(item.get("title") or "")).strip()
        content = _EM_TAG_RE.sub("", str(item.get("content") or "")).strip()
        if not title and not content:
            continue
        code = str(item.get("code") or "").strip()
        rows.append({
            "新闻标题": title,
            "新闻内容": content,
            "发布时间": str(item.get("date") or ""),
            "文章来源": str(item.get("mediaName") or ""),
            "新闻链接": f"http://finance.eastmoney.com/a/{code}.html" if code else "",
        })
    if not rows:
        return None
    return pd.DataFrame(rows)


def _eastmoney_stock_news_fallback(code: str) -> pd.DataFrame | None:
    """Fetch per-stock news from the Eastmoney search API directly, bypassing proxies.

    AKShare's ``stock_news_em`` issues plain ``requests`` calls that inherit
    the OS/environment proxy; when that proxy routes
    ``search-api-web.eastmoney.com`` through an overseas node the request is
    rejected and per-stock news becomes "访问失败". Mirrors the hot-rank
    fallback: disable environment proxies, send a browser UA, retry.
    """
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Referer": f"https://so.eastmoney.com/news/s?keyword={code}",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    inner_param = {
        "uid": "",
        "keyword": code,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": 100,
                "preTag": "<em>",
                "postTag": "</em>",
            }
        },
    }
    params = {
        "cb": "cb",
        "param": json.dumps(inner_param, ensure_ascii=False),
    }

    last_error: Exception | None = None
    for _ in range(3):
        session = requests.Session()
        session.trust_env = False
        try:
            resp = session.get(
                "https://search-api-web.eastmoney.com/search/jsonp",
                params=params,
                headers=headers,
                timeout=8,
            )
            resp.raise_for_status()
            text = resp.text.strip()
            # JSONP: cb({...}) — slice out the JSON payload.
            payload = json.loads(text[text.index("(") + 1 : text.rindex(")")])
            items = (payload.get("result") or {}).get("cmsArticleWebOld") or []
            df = _em_news_df(items)
            if df is not None:
                return df
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        logger.info("Eastmoney stock-news fallback failed: %s", last_error)
    return None


def get_news(
    ticker: Annotated[str, "ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd"],
    end_date: Annotated[str, "End date in yyyy-mm-dd"],
) -> str:
    """Per-stock news via ``stock_news_em`` (eastmoney), with a direct fallback."""
    ak = ak_lazy_import()
    code = normalize_for_akshare(ticker)

    source = "AKShare stock_news_em"
    fetch_error: Exception | None = None
    raw = None
    try:
        raw = akshare_call(ak.stock_news_em, symbol=code)
    except Exception as exc:
        fetch_error = exc

    if raw is None or raw.empty:
        fallback = _eastmoney_stock_news_fallback(code)
        if fallback is not None and not fallback.empty:
            raw = fallback
            source = "Eastmoney search API (direct fallback)"
        elif fetch_error is not None:
            return f"Failed to fetch A-share news for {ticker}: {fetch_error}"
        else:
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
             f"# Source: {source}",
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


def _flash_items_from_df(df: pd.DataFrame | None, limit: int) -> list[dict[str, str]]:
    """Normalize a flash-news DataFrame into ``{title, content, datetime}`` rows.

    Handles the column variants across vendors: CLS uses 标题/内容/发布日期+发布时间,
    eastmoney uses 标题/摘要/发布时间, THS uses 标题/内容/发布时间.
    """
    if df is None or df.empty:
        return []
    col_title = next((c for c in df.columns if "标题" in c), None)
    col_body = next((c for c in df.columns if "内容" in c or "摘要" in c), None)
    col_date = next((c for c in df.columns if "日期" in c), None)
    col_time = next((c for c in df.columns if "时间" in c), None)
    items: list[dict[str, str]] = []
    for _, row in df.head(limit).iterrows():
        title = _clean_snippet(row[col_title], 80) if col_title else ""
        content = _clean_snippet(row[col_body], 200) if col_body else ""
        if not title and not content:
            continue
        stamp = " ".join(
            str(row[c]).strip() for c in (col_date, col_time) if c and pd.notna(row[c])
        )
        items.append({
            "title": title or content[:50],
            "content": content,
            "datetime": stamp,
        })
    return items


def get_topic_news(
    query: Annotated[str, "industry, commodity, or theme keyword"],
    curr_date: Annotated[str, "current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "days to look back"] = 14,
    limit: Annotated[int, "max items"] = 20,
) -> list[dict[str, str]]:
    """Keyword-focused financial news from Eastmoney's article search.

    Unlike the market flash feed, this preserves the user's sector/theme
    keyword so focused reports do not depend on a lucky hit in the day's
    market-wide headlines.
    """
    query = str(query or "").strip()
    if not query:
        return []
    raw = _eastmoney_stock_news_fallback(query)
    if raw is None or raw.empty:
        return []
    col_time = next((c for c in raw.columns if "时间" in c or "日期" in c), None)
    if col_time:
        raw = raw.copy()
        raw[col_time] = pd.to_datetime(raw[col_time], errors="coerce")
        end_dt = pd.to_datetime(curr_date) + timedelta(days=1)
        start_dt = pd.to_datetime(curr_date) - timedelta(days=max(1, int(look_back_days)) - 1)
        raw = raw[(raw[col_time] >= start_dt) & (raw[col_time] < end_dt)]
    return _flash_items_from_df(raw, max(1, int(limit)))


def get_market_news_flash(
    curr_date: Annotated[str, "current date yyyy-mm-dd"],
    limit: Annotated[int, "max items"] = 25,
) -> list[dict[str, str]]:
    """Market-wide news flash as structured items for the market_overview skill.

    Walks a chain of financial flash feeds — CLS telegraph (财联社电报) →
    eastmoney global flash (东财快讯) → THS live flash (同花顺直播) — and only
    falls back to CCTV evening news scripts when every market feed fails,
    since CCTV content is mostly general politics rather than market news.
    Each item is ``{"title", "content", "datetime"}`` capped for prompt safety.
    """
    ak = ak_lazy_import()
    limit = max(1, int(limit))

    sources = (
        ("stock_info_global_cls", lambda: akshare_call(ak.stock_info_global_cls, symbol="全部")),
        ("stock_info_global_em", lambda: akshare_call(ak.stock_info_global_em)),
        ("stock_info_global_ths", lambda: akshare_call(ak.stock_info_global_ths)),
    )
    for name, fetch in sources:
        if not hasattr(ak, name):
            continue
        try:
            df = fetch()
        except Exception:
            continue
        items = _flash_items_from_df(df, limit)
        if items:
            return items

    # Last resort: walk back up to 3 days of CCTV scripts.
    items = []
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d").date()
    for i in range(3):
        day = end_dt - timedelta(days=i)
        try:
            df = akshare_call(ak.news_cctv, date=day.strftime("%Y%m%d"))
        except Exception:
            continue
        if df is None or df.empty:
            continue
        col_title = next((c for c in df.columns if "title" in c.lower() or "标题" in c), None)
        col_body = next((c for c in df.columns if "content" in c.lower() or "内容" in c), None)
        for _, row in df.iterrows():
            title = _clean_snippet(row[col_title], 80) if col_title else ""
            content = _clean_snippet(row[col_body], 200) if col_body else ""
            if not title and not content:
                continue
            items.append({
                "title": title or content[:50],
                "content": content,
                "datetime": day.isoformat(),
            })
            if len(items) >= limit:
                return items
    return items


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
