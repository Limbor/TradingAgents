"""A-share only market microstructure data.

- Limit-up / limit-down status & ST flags
- Northbound (Stock Connect) flow
- Margin trading / short balance
- Upcoming share unlock (解禁) schedule
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, Any

import pandas as pd

from .akshare_common import ak_lazy_import, akshare_call, df_to_csv_report
from .symbol_utils import normalize_cn_display, normalize_for_akshare

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
        else:
            _append_hot_rank_fallback(sections, code, display, "AKShare wrapper returned no rank data.")
    except Exception as exc:
        _append_hot_rank_fallback(sections, code, display, f"AKShare wrapper unavailable: {exc}")

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
                fallback = _eastmoney_board_heat_fallback(fn_name, top_n)
                if fallback is not None and not fallback.empty:
                    sections.append(
                        df_to_csv_report(
                            fallback,
                            title=f"{title} around {curr_date} (Eastmoney direct fallback)",
                            header_lines=[
                                "AKShare wrapper returned no data; fetched Eastmoney board rank directly.",
                                "Network policy: direct connection, system proxy bypassed for this request.",
                            ],
                        )
                    )
                else:
                    sections.append(f"# {title}: no data returned")
                continue
            sections.append(df_to_csv_report(board.head(max(1, min(top_n, 50))), title=f"{title} around {curr_date}"))
        except Exception as exc:
            fallback = _eastmoney_board_heat_fallback(fn_name, top_n)
            if fallback is not None and not fallback.empty:
                sections.append(
                    df_to_csv_report(
                        fallback,
                        title=f"{title} around {curr_date} (Eastmoney direct fallback)",
                        header_lines=[
                            f"AKShare wrapper unavailable: {exc}",
                            "Network policy: direct connection, system proxy bypassed for this request.",
                        ],
                    )
                )
            else:
                sections.append(f"# {title} unavailable: {exc}")

    return "\n\n".join(sections) if sections else f"No theme heat data available for {display}"


def _eastmoney_hot_rank_fallback() -> pd.DataFrame | None:
    """Fetch the Eastmoney retail popularity rank directly, bypassing proxies.

    AKShare's ``stock_hot_rank_em`` issues plain ``requests`` calls that inherit
    the OS/environment proxy; when that proxy routes ``emappdata.eastmoney.com``
    through an overseas node the request is rejected and the whole popularity
    rank becomes "unavailable". This fallback disables environment proxies for
    the request, sends a browser UA, and retries.

    The rank endpoint is the mandatory step; the price-enrichment step is best
    effort and its failure does not discard the rank.
    """
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Referer": "https://guba.eastmoney.com/rank/",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    payload = {
        "appId": "appId01",
        "globalId": "786e4c21-70dc-435a-93bb-38",
        "marketType": "",
        "pageNo": 1,
        "pageSize": 100,
    }

    rank_rows: list[dict[str, Any]] | None = None
    last_error: Exception | None = None
    for _ in range(3):
        session = requests.Session()
        session.trust_env = False
        try:
            resp = session.post(
                "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                json=payload,
                headers=headers,
                timeout=8,
            )
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or []
            if data:
                rank_rows = data
                break
        except Exception as exc:
            last_error = exc
    if not rank_rows:
        if last_error is not None:
            logger.info("Eastmoney hot-rank fallback failed: %s", last_error)
        return None

    rank_df = pd.DataFrame(rank_rows)
    if "sc" not in rank_df.columns or "rk" not in rank_df.columns:
        logger.info("Eastmoney hot-rank fallback unexpected schema: %s", list(rank_df.columns))
        return None
    result = pd.DataFrame(
        {
            "当前排名": pd.to_numeric(rank_df["rk"], errors="coerce"),
            "代码": rank_df["sc"].astype(str),
        }
    )

    # Optional: enrich with name + price via push2. Tolerate failure so the
    # rank itself is still returned when only the quote endpoint is blocked.
    try:
        marks = [
            ("0." + str(item)[2:]) if "SZ" in str(item) else ("1." + str(item)[2:])
            for item in rank_df["sc"]
        ]
        qsession = requests.Session()
        qsession.trust_env = False
        params = {
            "ut": "f057cbcbce2a86e2866ab8877db1d059",
            "fltt": "2",
            "invt": "2",
            "fields": "f14,f3,f12,f2",
            "secids": ",".join(marks) + ",?v=08926209912590994",
        }
        qresp = qsession.get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            params=params,
            headers=headers,
            timeout=8,
        )
        qresp.raise_for_status()
        diff = (qresp.json() or {}).get("data", {}).get("diff") or []
        if diff:
            quote = pd.DataFrame(diff).rename(
                columns={"f14": "股票名称", "f3": "涨跌幅", "f12": "_qcode", "f2": "最新价"}
            )
            for col in ("股票名称", "最新价", "涨跌幅"):
                if col in quote.columns:
                    result[col] = quote[col].values
            if "最新价" in result.columns and "涨跌幅" in result.columns:
                result["最新价"] = pd.to_numeric(result["最新价"], errors="coerce")
                result["涨跌幅"] = pd.to_numeric(result["涨跌幅"], errors="coerce")
                result["涨跌额"] = result["最新价"] * result["涨跌幅"] / 100
    except Exception as exc:
        logger.info("Eastmoney hot-rank price enrichment skipped: %s", exc)

    result["source"] = "eastmoney_direct:hotrank"
    return result


def _append_hot_rank_fallback(
    sections: list[str], code: str, display: str, reason: str
) -> None:
    """Append a direct-connection hot-rank section, or an unavailable note.

    Shared by the empty-data and exception paths of ``get_theme_heat`` (and by
    ``get_social_sentiment``) so the popularity rank is not silently lost when
    AKShare inherits a broken/system proxy.
    """
    fb = _eastmoney_hot_rank_fallback()
    if fb is not None and not fb.empty:
        fb_row = _filter_code(fb, code)
        if not fb_row.empty:
            sections.append(
                df_to_csv_report(
                    fb_row,
                    title=f"Retail heat rank for {display} (Eastmoney direct fallback)",
                    header_lines=[
                        reason,
                        "Network policy: direct connection, system proxy bypassed for this request.",
                    ],
                )
            )
            return
    sections.append(f"# Retail heat rank unavailable: {reason}")


def _eastmoney_board_heat_fallback(fn_name: str, top_n: int) -> pd.DataFrame | None:
    """Fetch Eastmoney concept/industry board heat directly.

    AKShare's Eastmoney helpers can inherit the macOS/system proxy and fail with
    ProxyError, or be rejected by Eastmoney without browser-like headers. This
    fallback intentionally disables environment proxies for this request and
    uses a compact field set to reduce rejection risk.
    """

    mapping = {
        "stock_board_concept_name_em": ("concept", "m:90 t:3 f:!50"),
        "stock_board_industry_name_em": ("industry", "m:90 t:2 f:!50"),
    }
    item = mapping.get(fn_name)
    if item is None:
        return None
    board_type, fs = item
    try:
        import requests

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        params = {
            "pn": 1,
            "pz": max(1, min(int(top_n), 50)),
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": fs,
            "fields": "f12,f14,f3,f4,f8,f2,f20,f21,f62,f104,f105",
        }
        last_error: Exception | None = None
        for host in ("79.push2.eastmoney.com", "17.push2.eastmoney.com", "push2.eastmoney.com"):
            session = requests.Session()
            session.trust_env = False
            try:
                response = session.get(
                    f"https://{host}/api/qt/clist/get",
                    params=params,
                    headers=headers,
                    timeout=8,
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                rows = (payload.get("data") or {}).get("diff") or []
                if not rows:
                    continue
                df = pd.DataFrame(rows)
                return _normalize_eastmoney_board_df(df, board_type, host)
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            logger.info("Eastmoney %s fallback failed: %s", board_type, last_error)
    except Exception as exc:
        logger.info("Eastmoney %s fallback unavailable: %s", board_type, exc)
    return None


def _normalize_eastmoney_board_df(df: pd.DataFrame, board_type: str, host: str) -> pd.DataFrame:
    columns = {
        "f12": "板块代码",
        "f14": "板块名称",
        "f3": "涨跌幅",
        "f4": "涨跌额",
        "f8": "换手率",
        "f2": "最新价",
        "f20": "总市值",
        "f21": "流通市值",
        "f62": "主力净流入",
        "f104": "上涨家数",
        "f105": "下跌家数",
    }
    result = df.rename(columns={key: value for key, value in columns.items() if key in df.columns})
    keep = [value for value in columns.values() if value in result.columns]
    result = result[keep].copy()
    result.insert(0, "类型", "概念板块" if board_type == "concept" else "行业板块")
    result["source"] = f"eastmoney_direct:{host}"
    return result


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
