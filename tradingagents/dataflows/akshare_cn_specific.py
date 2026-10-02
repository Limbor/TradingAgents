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
        elif hot is not None and not hot.empty:
            # List fetched fine; the stock just ranks below the top 100.
            _append_hot_rank_fallback(
                sections, code, display,
                f"{display} is outside the pan-market top-100 hot list.",
                try_list=False,
            )
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


def _hot_rank_secid(code: str) -> str:
    """Map a bare 6-digit A-share code to Eastmoney's SH/SZ/BJ-prefixed form."""
    if code.startswith(("6", "9", "5")):
        return f"SH{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SZ{code}"


def _eastmoney_hot_rank_latest(code: str) -> pd.DataFrame | None:
    """Fetch the exact popularity rank for a single stock via getCurrentLatest.

    The pan-market list endpoint only serves the top 100 names; a stock ranked
    below that never appears there, so filtering the list yields nothing even
    when the network is healthy. This per-stock endpoint returns the precise
    rank (out of ~5,500 stocks) for **any** ticker, plus day-over-day and
    history rank deltas — exactly the crowding signal the sentiment analysis
    needs. Direct connection, environment proxies bypassed.
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
        "srcSecurityCode": _hot_rank_secid(code),
    }
    last_error: Exception | None = None
    for _ in range(3):
        session = requests.Session()
        session.trust_env = False
        try:
            resp = session.post(
                "https://emappdata.eastmoney.com/stockrank/getCurrentLatest",
                json=payload,
                headers=headers,
                timeout=8,
            )
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or {}
            if data.get("rank") is not None:
                return pd.DataFrame(
                    [
                        {
                            "代码": str(data.get("srcSecurityCode", code)),
                            "当前排名": data.get("rank"),
                            "排名较昨日变动": data.get("rankChange"),
                            "历史排名变动": data.get("hisRankChange"),
                            "全市场股票数": data.get("marketAllCount"),
                            "排名计算时间": data.get("calcTime"),
                            "source": "eastmoney_direct:hotrank_latest",
                        }
                    ]
                )
            return None
        except Exception as exc:
            last_error = exc
        finally:
            session.close()
    if last_error is not None:
        logger.info("Eastmoney per-stock hot-rank fallback failed: %s", last_error)
    return None


def _append_hot_rank_fallback(
    sections: list[str], code: str, display: str, reason: str, *, try_list: bool = True
) -> None:
    """Append a direct-connection hot-rank section, or an unavailable note.

    Shared by the empty-data and exception paths of ``get_theme_heat`` (and by
    ``get_social_sentiment``) so the popularity rank is not silently lost when
    AKShare inherits a broken/system proxy.

    Two-stage fallback: the top-100 list (rich: name/price) first, then the
    per-stock getCurrentLatest endpoint which covers stocks ranked below 100.
    Pass ``try_list=False`` when the top-100 list was already fetched and the
    stock is known to be absent from it, skipping the redundant list request.
    """
    if try_list:
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
    latest = _eastmoney_hot_rank_latest(code)
    if latest is not None and not latest.empty:
        sections.append(
            df_to_csv_report(
                latest,
                title=f"Retail heat rank for {display} (Eastmoney per-stock direct fallback)",
                header_lines=[
                    reason,
                    "Stock is outside the pan-market top-100 hot list; exact rank fetched via per-stock endpoint.",
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
    fallback disables environment proxies, leaving TUN routing to Clash, and
    uses a compact field set to reduce rejection risk. The bare push2 host is
    retried so a Clash round-robin group can move each new connection to a
    different egress when Eastmoney has temporarily blocked one address.
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
            "Connection": "close",
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
        hosts = (
            "79.push2.eastmoney.com",
            "17.push2.eastmoney.com",
            "push2.eastmoney.com",
            "push2.eastmoney.com",
            "push2.eastmoney.com",
            "push2.eastmoney.com",
            "push2.eastmoney.com",
            "push2.eastmoney.com",
        )
        for host in hosts:
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
            finally:
                session.close()
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


def _sina_board_heat_fallback(
    ak: Any,
    board_type: str = "industry",
) -> pd.DataFrame | None:
    """Sina sector/concept spot as a non-Eastmoney board-heat source.

    Used when both AKShare's Eastmoney helper and the direct Eastmoney
    endpoint are unreachable (e.g. proxy drops eastmoney.com). Sina exposes
    both ``新浪行业`` and ``概念`` snapshots but has no main-inflow column;
    columns are normalized so downstream 板块名称/涨跌幅 matching keeps working.
    """
    fn = getattr(ak, "stock_sector_spot", None)
    if fn is None:
        return None
    try:
        indicator = "概念" if board_type == "concept" else "新浪行业"
        raw = akshare_call(fn, indicator=indicator)
    except Exception as exc:
        logger.info("Sina sector fallback failed: %s", exc)
        return None
    if raw is None or raw.empty or "板块" not in raw.columns or "涨跌幅" not in raw.columns:
        return None
    df = pd.DataFrame({
        "板块名称": raw["板块"].astype(str).str.strip(),
        "涨跌幅": pd.to_numeric(raw["涨跌幅"], errors="coerce"),
    })
    if "总成交额" in raw.columns:
        df["总成交额"] = pd.to_numeric(raw["总成交额"], errors="coerce")
    if "股票名称" in raw.columns:
        df["领涨股票"] = raw["股票名称"].astype(str).str.strip()
    df = df.dropna(subset=["涨跌幅"]).sort_values("涨跌幅", ascending=False).reset_index(drop=True)
    if df.empty:
        return None
    df.insert(0, "类型", "概念板块" if board_type == "concept" else "行业板块")
    df["source"] = "sina_concept_spot" if board_type == "concept" else "sina_sector_spot"
    return df


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
        df = akshare_call(ak.stock_restricted_release_queue_em, symbol=code)
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


# ---------------------------------------------------------------------------
# Market-level helpers (market_overview skill)
#
# Unlike the per-ticker helpers above (markdown/CSV reports for agent
# prompts), these return structured dict/list payloads consumed by the
# market_overview skill and persisted into its artifact payload.
# ---------------------------------------------------------------------------


def _tushare_pro_or_none():
    """Best-effort TuShare client; None when token/package is unavailable."""
    try:
        from .tushare_common import get_pro_api

        return get_pro_api()
    except Exception as exc:
        logger.info("tushare unavailable: %s", exc)
        return None


def _tushare_index_daily(ts_code: str, curr_date: str) -> pd.DataFrame | None:
    """TuShare index daily bars normalized to the Eastmoney frame shape.

    Returns ascending rows with ``date``/``close``/``amount`` columns
    (amount converted from 千元 to 元 to match the Eastmoney unit).
    """
    pro = _tushare_pro_or_none()
    if pro is None:
        return None
    from .tushare_common import tushare_call

    try:
        raw = tushare_call(
            pro.index_daily,
            ts_code=ts_code,
            start_date=(pd.to_datetime(curr_date) - timedelta(days=60)).strftime("%Y%m%d"),
            end_date=curr_date.replace("-", ""),
        )
    except Exception as exc:
        logger.info("tushare index_daily %s failed: %s", ts_code, exc)
        return None
    if raw is None or raw.empty or "close" not in raw.columns:
        return None
    raw = raw.sort_values("trade_date")
    df = pd.DataFrame({
        "date": pd.to_datetime(raw["trade_date"], errors="coerce"),
        "close": pd.to_numeric(raw["close"], errors="coerce"),
    })
    if "amount" in raw.columns:
        df["amount"] = pd.to_numeric(raw["amount"], errors="coerce") * 1000.0
    return df.dropna(subset=["close"])


def _tushare_unlock_events(curr_date: str, days_ahead: int) -> list[dict[str, Any]]:
    """TuShare ``share_float`` as the unlock-calendar fallback.

    Rows are per-shareholder; aggregate to one event per stock/date. No
    market-value column is available, so ``market_value`` stays None.
    """
    pro = _tushare_pro_or_none()
    if pro is None:
        return []
    from .tushare_common import tushare_call

    try:
        raw = tushare_call(
            pro.share_float,
            start_date=curr_date.replace("-", ""),
            end_date=(pd.to_datetime(curr_date) + timedelta(days=max(1, int(days_ahead)))).strftime("%Y%m%d"),
            fields="ts_code,float_date,float_share,float_ratio",
        )
    except Exception as exc:
        logger.info("tushare share_float failed: %s", exc)
        return []
    if raw is None or raw.empty:
        return []
    raw = raw.copy()
    raw["float_ratio"] = pd.to_numeric(raw["float_ratio"], errors="coerce")
    grouped = (
        raw.groupby(["ts_code", "float_date"], as_index=False)["float_ratio"].sum()
    )
    # Surface the largest unlocks (by % of shares) inside the window.
    grouped = grouped.sort_values("float_ratio", ascending=False).head(40)
    grouped = grouped.sort_values("float_date")

    names: dict[str, str] = {}
    try:
        basic = tushare_call(pro.stock_basic, fields="ts_code,name")
        if basic is not None and not basic.empty:
            names = dict(zip(basic["ts_code"], basic["name"], strict=False))
    except Exception as exc:
        logger.info("tushare stock_basic failed: %s", exc)

    events: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        ts_code = str(row["ts_code"]).strip()
        date_raw = str(row["float_date"]).strip()
        date_str = (
            f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}" if len(date_raw) == 8 else date_raw
        )
        events.append({
            "symbol": ts_code.split(".")[0],
            "name": names.get(ts_code, ts_code),
            "date": date_str,
            "market_value": None,
        })
    return events


def get_board_heat(
    curr_date: Annotated[str, "date yyyy-mm-dd"],
    top_n: Annotated[int, "max boards to fetch"] = 50,
    board_type: Annotated[str, "'industry' or 'concept'"] = "industry",
) -> pd.DataFrame | None:
    """Full board heat table (行业/概念板块) as a DataFrame.

    Returns the raw normalized DataFrame so callers can rank/slice
    (e.g. top gainers + bottom losers) instead of a pre-truncated report.
    Falls back to the direct Eastmoney endpoint when AKShare fails.
    """
    ak = ak_lazy_import()
    fn_name = (
        "stock_board_industry_name_em" if board_type == "industry"
        else "stock_board_concept_name_em"
    )
    fn = getattr(ak, fn_name, None)
    df: pd.DataFrame | None = None
    if fn is not None:
        try:
            df = akshare_call(fn)
        except Exception as exc:
            logger.info("%s failed (%s); trying Eastmoney direct fallback", fn_name, exc)
            df = None
    if df is None or df.empty:
        df = _eastmoney_board_heat_fallback(fn_name, top_n)
    if df is None or df.empty:
        df = _sina_board_heat_fallback(ak, board_type=board_type)
    if df is None or df.empty:
        return None
    return df.head(max(1, int(top_n))).copy()


def _concept_name_key(value: Any) -> str:
    text = str(value or "").strip()
    for suffix in ("概念板块", "概念", "板块"):
        if text.endswith(suffix):
            text = text.removesuffix(suffix).strip()
            break
    return text.casefold()


def _a_share_ts_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text.startswith(("SH", "SZ", "BJ")) and "." not in text:
        prefix, text = text[:2], text[2:]
        if text.isdigit() and len(text) == 6:
            return f"{text}.{prefix}"
    if "." in text:
        code, exchange = text.split(".", 1)
        if code.isdigit() and len(code) == 6 and exchange in {"SH", "SZ", "BJ"}:
            return f"{code}.{exchange}"
        return None
    if not text.isdigit() or len(text) != 6:
        return None
    if text.startswith(("4", "8", "92")):
        exchange = "BJ"
    elif text.startswith("6"):
        exchange = "SH"
    else:
        exchange = "SZ"
    return f"{text}.{exchange}"


def _concept_rows(frame: pd.DataFrame | None) -> list[dict[str, str]]:
    if frame is None or frame.empty:
        return []
    code_col = next(
        (col for col in ("代码", "code", "股票代码", "symbol") if col in frame.columns),
        None,
    )
    name_col = next(
        (col for col in ("名称", "name", "股票名称") if col in frame.columns),
        None,
    )
    if code_col is None:
        return []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, row in frame.iterrows():
        ts_code = _a_share_ts_code(row.get(code_col))
        if not ts_code or ts_code in seen:
            continue
        seen.add(ts_code)
        rows.append({
            "ts_code": ts_code,
            "name": str(row.get(name_col) or "").strip() if name_col else "",
        })
    return rows


def get_concept_constituents(concept_name: str) -> dict[str, Any]:
    """Resolve a current investor-facing concept into exact A-share members.

    Sina is preferred because it shares the fallback taxonomy used by the
    Market matrix. Eastmoney/AKShare remains a fallback. Both providers expose
    current membership only, so callers must disclose this for historical runs.
    """
    requested = str(concept_name or "").strip()
    key = _concept_name_key(requested)
    if not key:
        return {"concept": requested, "source_name": None, "source": None, "rows": []}
    ak = ak_lazy_import()

    spot_fn = getattr(ak, "stock_sector_spot", None)
    detail_fn = getattr(ak, "stock_sector_detail", None)
    if spot_fn is not None and detail_fn is not None:
        try:
            spot = akshare_call(spot_fn, indicator="概念")
            if spot is not None and not spot.empty and {"label", "板块"}.issubset(spot.columns):
                matches = spot[spot["板块"].map(_concept_name_key) == key]
                if len(matches) == 1:
                    matched = matches.iloc[0]
                    detail = akshare_call(detail_fn, sector=str(matched["label"]).strip())
                    rows = _concept_rows(detail)
                    if rows:
                        return {
                            "concept": requested,
                            "source_name": str(matched["板块"]).strip(),
                            "source": "sina_concept_members",
                            "rows": rows,
                        }
        except Exception as exc:
            logger.info("Sina concept constituents failed for %s: %s", requested, exc)

    eastmoney_fn = getattr(ak, "stock_board_concept_cons_em", None)
    if eastmoney_fn is not None:
        candidates = list(dict.fromkeys([requested, requested.removesuffix("板块")]))
        for candidate in candidates:
            if not candidate:
                continue
            try:
                detail = akshare_call(eastmoney_fn, symbol=candidate)
                rows = _concept_rows(detail)
                if rows:
                    return {
                        "concept": requested,
                        "source_name": candidate,
                        "source": "eastmoney_concept_members",
                        "rows": rows,
                    }
            except Exception as exc:
                logger.info(
                    "Eastmoney concept constituents failed for %s (%s): %s",
                    requested,
                    candidate,
                    exc,
                )

    return {"concept": requested, "source_name": None, "source": None, "rows": []}


def get_market_indices_overview(
    curr_date: Annotated[str, "date yyyy-mm-dd"],
) -> dict[str, Any]:
    """Headline index snapshot: SSE / SZSE / ChiNext.

    Returns ``{"indices": [...], "turnover_amount": float | None}`` where each
    index entry has close, pct_change, MA20 position, and a 20-day close
    series for frontend sparklines. Missing indices are skipped (degraded).
    """
    ak = ak_lazy_import()
    targets = [
        ("sh000001", "上证指数", "000001.SH"),
        ("sz399001", "深证成指", "399001.SZ"),
        ("sz399006", "创业板指", "399006.SZ"),
    ]
    indices: list[dict[str, Any]] = []
    turnover_amount = 0.0
    turnover_seen = False
    end_dt = pd.to_datetime(curr_date)

    for symbol, name, ts_code in targets:
        df = None
        try:
            df = akshare_call(ak.stock_zh_index_daily_em, symbol=symbol)
        except Exception as exc:
            logger.info("index %s (eastmoney) fetch failed: %s", symbol, exc)
        if df is None or df.empty or "close" not in df.columns:
            # TuShare mirror keeps the amount column, so turnover survives.
            df = _tushare_index_daily(ts_code, curr_date)
        if df is None or df.empty or "close" not in df.columns:
            # Sina mirror uses the same sh/sz symbol scheme; daily bars
            # lack the amount column, so turnover degrades gracefully.
            try:
                df = akshare_call(ak.stock_zh_index_daily, symbol=symbol)
            except Exception as exc:
                logger.info("index %s (sina) fetch failed: %s", symbol, exc)
                continue
        if df is None or df.empty or "close" not in df.columns:
            continue
        if "date" in df.columns:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df[df["date"] <= end_dt]
        df = df.tail(21)
        if df.empty:
            continue
        closes = [round(float(v), 2) for v in df["close"].tolist()]
        close = closes[-1]
        pct_change = (
            round((close / closes[-2] - 1) * 100, 2) if len(closes) >= 2 and closes[-2] else None
        )
        closes_20d = closes[-20:]
        ma20 = round(sum(closes_20d) / len(closes_20d), 2)
        indices.append({
            "code": symbol,
            "name": name,
            "close": close,
            "pct_change": pct_change,
            "above_ma20": close >= ma20,
            "ma20": ma20,
            "closes_20d": closes_20d,
        })
        # Two-market turnover = SSE composite + SZSE component amounts.
        if symbol in ("sh000001", "sz399001") and "amount" in df.columns:
            try:
                turnover_amount += float(df["amount"].iloc[-1])
                turnover_seen = True
            except Exception:
                pass

    return {
        "indices": indices,
        "turnover_amount": round(turnover_amount, 2) if turnover_seen else None,
    }


def get_market_breadth(
    curr_date: Annotated[str, "date yyyy-mm-dd"],
) -> dict[str, Any]:
    """Market breadth: advancers/decliners + limit-up/down/broken counts.

    Primary source is ``stock_market_activity_legu`` (赚钱效应分析). When it
    fails, falls back to the limit pools so at least limit counts survive.
    """
    ak = ak_lazy_import()
    breadth: dict[str, Any] = {
        "up": None, "down": None, "flat": None,
        "limit_up": None, "limit_down": None, "broken_limit": None,
    }

    def _to_int(value) -> int | None:
        try:
            return int(float(str(value).replace(",", "")))
        except Exception:
            return None

    try:
        df = akshare_call(ak.stock_market_activity_legu)
    except Exception as exc:
        logger.info("stock_market_activity_legu failed: %s", exc)
        df = None

    if df is not None and not df.empty and {"item", "value"}.issubset(df.columns):
        mapping = {
            "上涨": "up", "下跌": "down", "平盘": "flat",
            "涨停": "limit_up", "跌停": "limit_down",
        }
        for _, row in df.iterrows():
            item = str(row["item"]).strip()
            key = mapping.get(item)
            # "真实涨停/跌停" rows also contain the keyword; only take exact match.
            if key is not None and breadth.get(key) is None:
                breadth[key] = _to_int(row["value"])

    date_compact = curr_date.replace("-", "")
    if breadth["limit_up"] is None:
        try:
            zt = akshare_call(ak.stock_zt_pool_em, date=date_compact)
            breadth["limit_up"] = len(zt) if zt is not None else None
        except Exception:
            pass
    if breadth["limit_down"] is None:
        try:
            dt = akshare_call(ak.stock_zt_pool_dtgc_em, date=date_compact)
            breadth["limit_down"] = len(dt) if dt is not None else None
        except Exception:
            pass
    try:
        zb = akshare_call(ak.stock_zt_pool_zbgc_em, date=date_compact)
        breadth["broken_limit"] = len(zb) if zb is not None else None
    except Exception:
        pass

    return breadth


def get_northbound_summary(
    curr_date: Annotated[str, "date yyyy-mm-dd"],
) -> dict[str, Any]:
    """Northbound flow summary as structured data: latest + 5-day net (亿元).

    Disclosure policy changes may leave the series stale or empty; callers
    should treat ``None`` fields as "data unavailable", not an error.
    """
    ak = ak_lazy_import()
    try:
        df = akshare_call(ak.stock_hsgt_hist_em, symbol="北向资金")
    except Exception as exc:
        logger.info("northbound summary failed: %s", exc)
        return {"latest_net": None, "five_day_net": None, "latest_date": None}

    if df is None or df.empty:
        return {"latest_net": None, "five_day_net": None, "latest_date": None}

    col_time = next((c for c in df.columns if "日期" in c or "时间" in c), None)
    col_net = next((c for c in df.columns if "净买额" in c or "净流入" in c), None)
    if col_net is None:
        return {"latest_net": None, "five_day_net": None, "latest_date": None}

    if col_time:
        df = df.copy()
        df[col_time] = pd.to_datetime(df[col_time], errors="coerce")
        df = df[df[col_time] <= pd.to_datetime(curr_date)].sort_values(col_time)
    tail = df.tail(5)
    if tail.empty:
        return {"latest_net": None, "five_day_net": None, "latest_date": None}

    nets = pd.to_numeric(tail[col_net], errors="coerce").dropna()
    latest_net = round(float(nets.iloc[-1]), 2) if not nets.empty else None
    five_day_net = round(float(nets.sum()), 2) if not nets.empty else None
    latest_date = None
    if col_time and pd.notna(tail[col_time].iloc[-1]):
        latest_date = tail[col_time].iloc[-1].strftime("%Y-%m-%d")
    return {"latest_net": latest_net, "five_day_net": five_day_net, "latest_date": latest_date}


def get_market_unlock_overview(
    curr_date: Annotated[str, "date yyyy-mm-dd"],
    days_ahead: Annotated[int, "lookahead window in days"] = 14,
) -> list[dict[str, Any]]:
    """Market-wide upcoming unlock (解禁) events within ``days_ahead`` days."""
    ak = ak_lazy_import()
    start_dt = pd.to_datetime(curr_date)
    end_dt = start_dt + timedelta(days=max(1, int(days_ahead)))
    df = None
    try:
        # Market-wide per-stock detail; the queue_em endpoint is per-ticker
        # (defaults to a single stock) and must not be used here.
        df = akshare_call(
            ak.stock_restricted_release_detail_em,
            start_date=start_dt.strftime("%Y%m%d"),
            end_date=end_dt.strftime("%Y%m%d"),
        )
    except Exception as exc:
        logger.info("unlock overview failed (%s); trying tushare fallback", exc)

    if df is None or df.empty:
        return _tushare_unlock_events(curr_date, days_ahead)

    col_code = next((c for c in df.columns if "代码" in c), None)
    col_name = next((c for c in df.columns if "简称" in c or "名称" in c), None)
    col_time = next((c for c in df.columns if "日期" in c or "时间" in c), None)
    col_value = next((c for c in df.columns if "市值" in c), None)
    if col_time is None:
        return []

    df = df.copy()
    df[col_time] = pd.to_datetime(df[col_time], errors="coerce")
    df = df[(df[col_time] >= start_dt) & (df[col_time] <= end_dt)].sort_values(col_time)

    events: list[dict[str, Any]] = []
    for _, row in df.head(40).iterrows():
        market_value = None
        if col_value is not None:
            try:
                market_value = round(float(row[col_value]), 2)
            except Exception:
                market_value = None
        events.append({
            "symbol": str(row[col_code]).strip() if col_code else "",
            "name": str(row[col_name]).strip() if col_name else "",
            "date": row[col_time].strftime("%Y-%m-%d") if pd.notna(row[col_time]) else "",
            "market_value": market_value,
        })
    return events
