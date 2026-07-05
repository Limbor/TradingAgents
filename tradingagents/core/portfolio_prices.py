"""Portfolio symbol resolution and latest close helpers."""

from __future__ import annotations

import asyncio
import logging
import re
from functools import lru_cache
from datetime import date, timedelta
from typing import Any

from tradingagents.core.trading_time import get_temporal_context
from tradingagents.dataflows.symbol_utils import detect_market

logger = logging.getLogger(__name__)

_KNOWN_CN_NAMES = {
    "贵州茅台": "600519.SH",
    "茅台": "600519.SH",
    "宁德时代": "300750.SZ",
    "紫金矿业": "601899.SH",
    "中际旭创": "300308.SZ",
    "寒武纪": "688256.SH",
    "兆易创新": "603986.SH",
    "比亚迪": "002594.SZ",
    "招商银行": "600036.SH",
    "平安银行": "000001.SZ",
    "中国平安": "601318.SH",
    "五粮液": "000858.SZ",
    "美的集团": "000333.SZ",
    "东方财富": "300059.SZ",
    "工业富联": "601138.SH",
    "长江电力": "600900.SH",
    "中国移动": "600941.SH",
    "中芯国际": "688981.SH",
    "海康威视": "002415.SZ",
    "迈瑞医疗": "300760.SZ",
}


def resolve_portfolio_symbol(raw: str) -> str:
    """Resolve user input to a canonical portfolio symbol.

    Supports common A-share names, 6-digit bare A-share codes, suffixed A-share
    codes, and US symbols. Name lookup intentionally starts with a small local
    dictionary so adding a holding does not require a network call.
    """

    text = str(raw or "").strip()
    if not text:
        raise ValueError("symbol is required")
    if text in _KNOWN_CN_NAMES:
        return _KNOWN_CN_NAMES[text]
    upper = text.upper()
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ|BJ|SS))?", upper)
    if match:
        code = match.group(1)
        suffix = match.group(2)
        if suffix:
            return f"{code}.{'SH' if suffix == 'SS' else suffix}"
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")):
            return f"{code}.BJ"
    return upper


def resolve_portfolio_name(symbol: str) -> str:
    """Resolve a display name for a canonical holding symbol.

    This is intentionally best-effort and non-fatal. It first uses the small
    built-in name map, then the cached AKShare A-share code/name table.
    """

    canonical = resolve_portfolio_symbol(symbol)
    for name, mapped in _KNOWN_CN_NAMES.items():
        if mapped == canonical:
            return name
    if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", canonical):
        code = canonical.split(".")[0]
        try:
            mapping = _cn_name_map()
            for name, mapped in mapping.items():
                if mapped == canonical or mapped.split(".")[0] == code:
                    return name
        except Exception as exc:
            logger.info("Portfolio name lookup unavailable for %s: %s", canonical, exc)
    return canonical


async def resolve_portfolio_symbol_async(raw: str) -> str:
    """Resolve symbol with optional dynamic CN name lookup."""

    resolved = resolve_portfolio_symbol(raw)
    if resolved != str(raw or "").strip().upper():
        return resolved
    text = str(raw or "").strip()
    if not _looks_like_cn_name(text):
        return resolved
    dynamic = await asyncio.to_thread(_resolve_cn_name_with_akshare, text)
    return dynamic or resolved


async def latest_close(symbol: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch latest close from MCP first, then local dataflows."""

    canonical = resolve_portfolio_symbol(symbol)
    market = detect_market(canonical)
    temporal_context = get_temporal_context(config, market=market)
    asof_date = date.fromisoformat(temporal_context.market_asof_date)
    mcp_quote = await _latest_close_from_mcp(canonical, config, asof_date)
    if mcp_quote is not None:
        return mcp_quote
    return await asyncio.to_thread(_latest_close_from_dataflows, canonical, asof_date)


def _looks_like_cn_name(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


@lru_cache(maxsize=1)
def _cn_name_map() -> dict[str, str]:
    from tradingagents.dataflows.akshare_common import akshare_call, ak_lazy_import

    ak = ak_lazy_import()
    fn = getattr(ak, "stock_info_a_code_name", None)
    if fn is None:
        return {}
    raw = akshare_call(fn)
    if raw is None or raw.empty:
        return {}
    result: dict[str, str] = {}
    for _, row in raw.iterrows():
        code = str(row.get("code") or row.get("代码") or "").strip()
        name = str(row.get("name") or row.get("名称") or "").strip()
        if len(code) == 6 and name:
            result[name] = resolve_portfolio_symbol(code)
    return result


def _resolve_cn_name_with_akshare(name: str) -> str | None:
    try:
        mapping = _cn_name_map()
    except Exception as exc:
        logger.info("AKShare CN name map unavailable: %s", exc)
        return None
    if name in mapping:
        return mapping[name]
    candidates = [symbol for stock_name, symbol in mapping.items() if name in stock_name]
    return candidates[0] if len(candidates) == 1 else None


async def _latest_close_from_mcp(symbol: str, config: dict[str, Any], end: date) -> dict[str, Any] | None:
    from tradingagents.core.mcp_client import get_mcp_client

    client = await get_mcp_client(config)
    if client is None:
        return None
    start = end - timedelta(days=30)
    payload = None
    last_error: Exception | None = None
    # StockManager tools historically accepted compact YYYYMMDD; some local
    # callers used ISO dates. Try compact first, then ISO for compatibility.
    for start_date, end_date in (
        (start.strftime("%Y%m%d"), end.strftime("%Y%m%d")),
        (start.isoformat(), end.isoformat()),
    ):
        try:
            payload = await client.get_stock_daily(
                [symbol],
                start_date,
                end_date,
            )
        except Exception as exc:
            last_error = exc
            continue
        if _latest_row_from_payload(payload, symbol) is not None:
            break
    if payload is None:
        if last_error is not None:
            logger.info("MCP latest close failed for %s: %s", symbol, last_error)
        return None
    row = _latest_row_from_payload(payload, symbol)
    if row is None:
        return None
    close = _first_float(row, "close", "Close", "收盘")
    if close is None:
        return None
    trade_date = str(row.get("trade_date") or row.get("date") or row.get("Date") or "")
    return {"symbol": symbol, "close": close, "trade_date": trade_date, "source": "stockmanager_mcp"}


def _latest_close_from_dataflows(symbol: str, end: date) -> dict[str, Any] | None:
    from tradingagents.dataflows.akshare_stock import load_ohlcv_cn
    from tradingagents.dataflows.y_finance import get_YFin_data_online

    market = detect_market(symbol)
    try:
        if market == "cn_a":
            data = load_ohlcv_cn(symbol, end.isoformat())
        else:
            start = (end - timedelta(days=21)).isoformat()
            data = get_YFin_data_online(symbol, start, end.isoformat())
    except Exception as exc:
        logger.info("Local latest close failed for %s: %s", symbol, exc)
        # Final lightweight fallback for A-shares: call AKShare directly with a
        # short window. This avoids failures caused by stale/empty framework
        # cache files or stricter cleaning in load_ohlcv_cn.
        if market == "cn_a":
            return _latest_close_from_akshare_direct(symbol, end)
        return None
    if data is None or data.empty or "Close" not in data.columns:
        if market == "cn_a":
            return _latest_close_from_akshare_direct(symbol, end)
        return None
    data = data.dropna(subset=["Close"])
    if data.empty:
        if market == "cn_a":
            return _latest_close_from_akshare_direct(symbol, end)
        return None
    row = data.iloc[-1]
    close = float(row["Close"])
    trade_date = row.get("Date", "")
    try:
        trade_date = trade_date.strftime("%Y-%m-%d")
    except AttributeError:
        trade_date = str(trade_date)
    return {"symbol": symbol, "close": close, "trade_date": trade_date, "source": "local_dataflows"}


def _latest_row_from_payload(payload: Any, symbol: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows") or payload.get("data") or payload.get(symbol)
    if isinstance(rows, dict):
        rows = (
            rows.get("rows")
            or rows.get("data")
            or rows.get(symbol)
            or rows.get(symbol.upper())
            or _flatten_symbol_rows(rows)
            or [rows]
        )
    if not isinstance(rows, list) or not rows:
        return None
    dict_rows = [row for row in rows if isinstance(row, dict)]
    if not dict_rows:
        return None
    return sorted(
        dict_rows,
        key=lambda row: str(row.get("trade_date") or row.get("date") or row.get("Date") or ""),
    )[-1]


def _flatten_symbol_rows(value: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in value.values():
        if isinstance(item, list):
            rows.extend(row for row in item if isinstance(row, dict))
        elif isinstance(item, dict):
            nested = item.get("rows") or item.get("data")
            if isinstance(nested, list):
                rows.extend(row for row in nested if isinstance(row, dict))
            else:
                rows.append(item)
    return rows


def _first_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _latest_close_from_akshare_direct(symbol: str, end: date) -> dict[str, Any] | None:
    try:
        from tradingagents.dataflows.akshare_common import akshare_call, ak_lazy_import
        from tradingagents.dataflows.symbol_utils import normalize_for_akshare

        ak = ak_lazy_import()
        code = normalize_for_akshare(symbol)
        start = end - timedelta(days=30)
        raw = akshare_call(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if raw is None or raw.empty:
            return None
        row = raw.dropna(subset=["收盘"]).iloc[-1]
        trade_date = str(row.get("日期") or "")
        return {
            "symbol": symbol,
            "close": float(row["收盘"]),
            "trade_date": trade_date,
            "source": "akshare_direct",
        }
    except Exception as exc:
        logger.info("Direct AKShare latest close failed for %s: %s", symbol, exc)
        return None
