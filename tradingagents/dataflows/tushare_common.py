"""Common helpers for TuShare-backed data flow implementations.

Provides a lazily-initialized ``pro_api`` singleton and a rate-limited
call wrapper. TuShare uses a token obtained via their website; we read
``TUSHARE_TOKEN`` env var by default, with override via
``config['tushare_token']``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

import pandas as pd

from .config import get_config
from .rate_limiter import acquire_tushare

logger = logging.getLogger(__name__)

_CALL_TIMEOUT = float(os.environ.get("TUSHARE_CALL_TIMEOUT", "30"))
_CALL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tushare-call")


class TuShareError(RuntimeError):
    """Base error for TuShare vendor operations."""


class TuShareAuthError(TuShareError):
    """Raised when the TuShare token is missing or invalid."""


class TuShareRateLimitError(TuShareError):
    """Raised when TuShare rejects a request due to credit / rate limits."""


_pro_api = None
_pro_api_lock = threading.Lock()


def _resolve_token() -> str:
    token = get_config().get("tushare_token") or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise TuShareAuthError(
            "TuShare token missing. Set TUSHARE_TOKEN env var or "
            "config['tushare_token']. Obtain one from https://tushare.pro/."
        )
    return str(token).strip()


def get_pro_api():
    """Return a cached TuShare ``pro_api`` client."""
    global _pro_api
    if _pro_api is not None:
        return _pro_api
    with _pro_api_lock:
        if _pro_api is not None:
            return _pro_api
        try:
            import tushare as ts  # type: ignore
        except ImportError as exc:
            raise TuShareError(
                "tushare is not installed. Install with `pip install tushare` "
                "or `uv sync`."
            ) from exc
        token = _resolve_token()
        # Pass the token directly. ``ts.set_token`` writes ``~/tk.csv`` and
        # breaks sandboxed desktop runs even though the API itself is usable.
        _pro_api = ts.pro_api(token)
        return _pro_api


def tushare_call(func: Callable[..., Any], *args, **kwargs) -> Any:
    """Invoke a TuShare endpoint under the global token bucket.

    **Any** exception from the remote call is converted to
    ``TuShareRateLimitError`` so that ``route_to_vendor`` always
    tries the next fallback vendor.
    """
    acquire_tushare()
    future = _CALL_EXECUTOR.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=_CALL_TIMEOUT)
    except FutureTimeoutError as exc:
        name = getattr(func, "__name__", str(func))
        logger.warning("tushare call %s timed out after %.0fs", name, _CALL_TIMEOUT)
        future.cancel()
        raise TuShareRateLimitError(
            f"{name} timed out after {_CALL_TIMEOUT:.0f}s (no response from upstream)"
        ) from exc
    except TuShareRateLimitError:
        raise
    except Exception as exc:
        raise TuShareRateLimitError(str(exc)) from exc


_fine_industry_cache: dict[str, str] = {}
_fine_industry_fetched_at: float = 0.0
_fine_industry_lock = threading.Lock()
_FINE_INDUSTRY_TTL_SECONDS = 24 * 3600.0

_standard_industry_catalog_cache: dict[tuple[str, str], dict[str, str]] = {}
_standard_industry_catalog_fetched_at: dict[tuple[str, str], float] = {}
_standard_industry_catalog_lock = threading.Lock()
_STANDARD_INDUSTRY_CATALOG_TTL_SECONDS = 24 * 3600.0
_standard_industry_denied_until: dict[tuple[str, str], float] = {}
_sw2021_member_cache: dict[str, tuple[str, str]] = {}
_sw2021_member_fetched_at: float = 0.0
_sw2021_member_lock = threading.Lock()


def _is_permission_error(exc: Exception) -> bool:
    text = str(exc).casefold()
    return any(marker in text for marker in ("没有接口", "访问权限", "permission", "not entitled"))


def _raise_if_industry_endpoint_denied(key: tuple[str, str]) -> None:
    if time.time() < _standard_industry_denied_until.get(key, 0.0):
        raise TuShareRateLimitError(f"{key[0]} {key[1]} endpoint not entitled (cached)")


def get_fine_industry_map() -> dict[str, str]:
    """ts_code -> TuShare 细分行业 (``stock_basic.industry``, 如 半导体/元器件).

    比申万一级（电子/医药生物...）细一档，用于 agent 侧的展示标注与
    行业限定的精细化匹配。全市场一次调用，进程内缓存 24h；TuShare
    不可用时返回上一次缓存（可能为空 dict），调用方按缺失降级。
    """
    global _fine_industry_fetched_at, _fine_industry_cache
    now = time.time()
    if _fine_industry_cache and now - _fine_industry_fetched_at < _FINE_INDUSTRY_TTL_SECONDS:
        return _fine_industry_cache
    with _fine_industry_lock:
        if _fine_industry_cache and now - _fine_industry_fetched_at < _FINE_INDUSTRY_TTL_SECONDS:
            return _fine_industry_cache
        try:
            raw = tushare_call(get_pro_api().stock_basic, fields="ts_code,industry")
        except Exception as exc:
            logger.info("tushare stock_basic (fine industry map) failed: %s", exc)
            return _fine_industry_cache
        if raw is None or raw.empty:
            return _fine_industry_cache
        mapping: dict[str, str] = {}
        for ts_code, industry in zip(raw["ts_code"], raw["industry"], strict=False):
            code = str(ts_code).strip().upper()
            name = str(industry).strip() if industry is not None else ""
            if code and name and name.lower() not in {"nan", "none"}:
                mapping[code] = name
        if mapping:
            _fine_industry_cache = mapping
            _fine_industry_fetched_at = now
        return _fine_industry_cache


def _standard_industry_catalog(
    taxonomy: str,
    level: str = "L1",
) -> dict[str, str]:
    """Return ``industry_code -> industry_name`` for a maintained taxonomy.

    CITICS is preferred for the investor-facing market panorama.  SW2021 is
    retained as a genuine standard-taxonomy fallback; provider concept names
    must never be silently presented as industries.
    """
    normalized_taxonomy = str(taxonomy or "").strip().upper()
    normalized_level = str(level or "L1").strip().upper()
    if normalized_taxonomy not in {"CITICS", "SW2021"}:
        raise ValueError(f"unsupported industry taxonomy: {taxonomy}")
    if normalized_level not in {"L1", "L2", "L3"}:
        raise ValueError(f"unsupported industry level: {level}")
    if normalized_taxonomy == "SW2021" and normalized_level != "L1":
        raise ValueError("SW2021 fallback currently supports L1 only")

    cache_key = (normalized_taxonomy, normalized_level)
    _raise_if_industry_endpoint_denied(cache_key)
    now = time.time()
    cached = _standard_industry_catalog_cache.get(cache_key)
    fetched_at = _standard_industry_catalog_fetched_at.get(cache_key, 0.0)
    if cached and now - fetched_at < _STANDARD_INDUSTRY_CATALOG_TTL_SECONDS:
        return cached

    with _standard_industry_catalog_lock:
        cached = _standard_industry_catalog_cache.get(cache_key)
        fetched_at = _standard_industry_catalog_fetched_at.get(cache_key, 0.0)
        if cached and now - fetched_at < _STANDARD_INDUSTRY_CATALOG_TTL_SECONDS:
            return cached

        pro = get_pro_api()
        catalog: dict[str, str] = {}
        if normalized_taxonomy == "CITICS":
            # ci_index_member exposes the complete L1/L2/L3 hierarchy.  Only
            # one current constituent snapshot is needed to recover the code
            # catalogue; pagination protects the >5000-stock boundary.
            code_col = f"{normalized_level.lower()}_code"
            name_col = f"{normalized_level.lower()}_name"
            offset = 0
            page_size = 5000
            while True:
                try:
                    frame = tushare_call(
                        pro.ci_index_member,
                        is_new="Y",
                        fields=f"{code_col},{name_col},ts_code,is_new",
                        limit=page_size,
                        offset=offset,
                    )
                except TuShareError as exc:
                    if _is_permission_error(exc):
                        _standard_industry_denied_until[cache_key] = (
                            time.time() + _STANDARD_INDUSTRY_CATALOG_TTL_SECONDS
                        )
                    raise
                if frame is None or frame.empty:
                    break
                if code_col not in frame.columns or name_col not in frame.columns:
                    break
                for code, name in zip(frame[code_col], frame[name_col], strict=False):
                    code_text = str(code or "").strip()
                    name_text = str(name or "").strip()
                    if code_text and name_text and name_text.lower() not in {"nan", "none"}:
                        catalog[code_text] = name_text
                if len(frame) < page_size:
                    break
                offset += len(frame)
        else:
            frame = tushare_call(pro.index_classify, level="L1", src="SW2021")
            if frame is not None and not frame.empty:
                name_col = "industry_name" if "industry_name" in frame.columns else "name"
                if "index_code" in frame.columns and name_col in frame.columns:
                    for code, name in zip(frame["index_code"], frame[name_col], strict=False):
                        code_text = str(code or "").strip()
                        name_text = str(name or "").strip()
                        if code_text and name_text and name_text.lower() not in {"nan", "none"}:
                            catalog[code_text] = name_text

        if catalog:
            _standard_industry_catalog_cache[cache_key] = catalog
            _standard_industry_catalog_fetched_at[cache_key] = now
        return catalog or cached or {}


def get_standard_industry_heat(
    trade_date: str,
    *,
    taxonomy: str = "CITICS",
    level: str = "L1",
) -> list[dict[str, Any]]:
    """Fetch one official index cross-section for a standard industry tree.

    The output is already normalized for ``market_overview`` and carries the
    taxonomy/code/level identity required by StockManager.  No fuzzy Chinese
    name mapping is involved.
    """
    normalized_taxonomy = str(taxonomy or "").strip().upper()
    normalized_level = str(level or "L1").strip().upper()
    catalog = _standard_industry_catalog(normalized_taxonomy, normalized_level)
    if not catalog:
        return []

    date_arg = str(trade_date or "").replace("-", "")
    pro = get_pro_api()
    if normalized_taxonomy == "CITICS":
        daily_key = ("CITICS_DAILY", normalized_level)
        _raise_if_industry_endpoint_denied(daily_key)
        try:
            frame = tushare_call(pro.ci_daily, trade_date=date_arg)
        except TuShareError as exc:
            if _is_permission_error(exc):
                _standard_industry_denied_until[daily_key] = (
                    time.time() + _STANDARD_INDUSTRY_CATALOG_TTL_SECONDS
                )
            raise
    elif normalized_taxonomy == "SW2021":
        daily_key = ("SW2021_DAILY", normalized_level)
        try:
            _raise_if_industry_endpoint_denied(daily_key)
            frame = tushare_call(pro.sw_daily, trade_date=date_arg)
        except TuShareError as exc:
            if _is_permission_error(exc):
                _standard_industry_denied_until[daily_key] = (
                    time.time() + _STANDARD_INDUSTRY_CATALOG_TTL_SECONDS
                )
            logger.info("tushare sw_daily unavailable; aggregating SW2021 members: %s", exc)
            return _aggregate_sw2021_constituent_heat(pro, date_arg, catalog)
    else:
        raise ValueError(f"unsupported industry taxonomy: {taxonomy}")
    if frame is None or frame.empty or "ts_code" not in frame.columns:
        return []

    pct_col = "pct_change" if "pct_change" in frame.columns else "pct_chg"
    rows: list[dict[str, Any]] = []
    for _, raw in frame.iterrows():
        code = str(raw.get("ts_code") or "").strip()
        name = catalog.get(code)
        if not name:
            continue  # drops L2/L3 rows from the all-level CITICS daily feed
        pct_value = raw.get(pct_col) if pct_col in frame.columns else None
        pct_change: float | None = None
        try:
            if pct_value is not None and str(pct_value).lower() not in {"nan", "none"}:
                pct_change = round(float(pct_value), 2)
        except (TypeError, ValueError):
            pct_change = None
        amount_value = raw.get("amount") if "amount" in frame.columns else None
        turnover_amount: float | None = None
        try:
            if amount_value is not None and str(amount_value).lower() not in {"nan", "none"}:
                turnover_amount = float(amount_value)
        except (TypeError, ValueError):
            turnover_amount = None
        rows.append({
            "industry": name,
            "source_name": name,
            "board_type": "industry",
            "taxonomy": normalized_taxonomy,
            "industry_code": code,
            "industry_level": normalized_level,
            "pct_change": pct_change,
            "main_inflow": None,
            "leader_stock": None,
            "turnover_amount": turnover_amount,
        })
    return rows


def _sw2021_current_members(pro: Any) -> dict[str, tuple[str, str]]:
    """Return ``ts_code -> (L1 code, L1 name)`` with a 24-hour cache."""
    global _sw2021_member_cache, _sw2021_member_fetched_at
    now = time.time()
    if (
        _sw2021_member_cache
        and now - _sw2021_member_fetched_at < _STANDARD_INDUSTRY_CATALOG_TTL_SECONDS
    ):
        return _sw2021_member_cache
    with _sw2021_member_lock:
        if (
            _sw2021_member_cache
            and now - _sw2021_member_fetched_at < _STANDARD_INDUSTRY_CATALOG_TTL_SECONDS
        ):
            return _sw2021_member_cache
        members: dict[str, tuple[str, str]] = {}
        offset = 0
        page_size = 2000
        while True:
            frame = tushare_call(
                pro.index_member_all,
                is_new="Y",
                fields="l1_code,l1_name,ts_code,is_new",
                limit=page_size,
                offset=offset,
            )
            if frame is None or frame.empty:
                break
            if not {"l1_code", "l1_name", "ts_code"}.issubset(frame.columns):
                break
            for _, row in frame.iterrows():
                ts_code = str(row.get("ts_code") or "").strip().upper()
                industry_code = str(row.get("l1_code") or "").strip()
                industry_name = str(row.get("l1_name") or "").strip()
                if ts_code and industry_code and industry_name:
                    members[ts_code] = (industry_code, industry_name)
            if len(frame) < page_size:
                break
            offset += len(frame)
        if members:
            _sw2021_member_cache = members
            _sw2021_member_fetched_at = now
        return members or _sw2021_member_cache


def _aggregate_sw2021_constituent_heat(
    pro: Any,
    trade_date: str,
    catalog: dict[str, str],
) -> list[dict[str, Any]]:
    """Compute an SW2021 L1 cross-section when ``sw_daily`` is not entitled.

    Returns are total-market-cap weighted when ``daily_basic`` is available;
    otherwise they are equal weighted.  Both paths use official SW2021 member
    codes, so this fallback never reintroduces provider concept categories.
    """
    members = _sw2021_current_members(pro)
    if not members:
        return []
    daily = tushare_call(
        pro.daily,
        trade_date=trade_date,
        fields="ts_code,trade_date,pct_chg,amount",
    )
    if daily is None or daily.empty or not {"ts_code", "pct_chg"}.issubset(daily.columns):
        return []

    market_caps: dict[str, float] = {}
    try:
        basic = tushare_call(
            pro.daily_basic,
            trade_date=trade_date,
            fields="ts_code,trade_date,total_mv",
        )
        if basic is not None and not basic.empty and {"ts_code", "total_mv"}.issubset(basic.columns):
            for code, value in zip(basic["ts_code"], basic["total_mv"], strict=False):
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if numeric > 0:
                    market_caps[str(code).strip().upper()] = numeric
    except TuShareError as exc:
        logger.info("daily_basic unavailable for SW2021 weighting; using equal weight: %s", exc)

    buckets: dict[str, list[tuple[float, float | None, float | None]]] = {}
    for _, row in daily.iterrows():
        ts_code = str(row.get("ts_code") or "").strip().upper()
        membership = members.get(ts_code)
        if membership is None:
            continue
        industry_code, _industry_name = membership
        if industry_code not in catalog:
            continue
        try:
            pct_change = float(row.get("pct_chg"))
        except (TypeError, ValueError):
            continue
        if pd.isna(pct_change):
            continue
        amount: float | None = None
        try:
            amount_value = float(row.get("amount"))
            if not pd.isna(amount_value):
                amount = amount_value
        except (TypeError, ValueError):
            pass
        buckets.setdefault(industry_code, []).append(
            (pct_change, market_caps.get(ts_code), amount)
        )

    rows: list[dict[str, Any]] = []
    for industry_code, values in buckets.items():
        weighted = [(pct, cap) for pct, cap, _ in values if cap is not None and cap > 0]
        if weighted:
            denominator = sum(cap for _, cap in weighted)
            pct_change = sum(pct * cap for pct, cap in weighted) / denominator
            method = "constituent_total_mv_weighted"
        else:
            pct_change = sum(pct for pct, _, _ in values) / len(values)
            method = "constituent_equal_weighted"
        amounts = [amount for _, _, amount in values if amount is not None]
        rows.append({
            "industry": catalog[industry_code],
            "source_name": catalog[industry_code],
            "board_type": "industry",
            "taxonomy": "SW2021",
            "industry_code": industry_code,
            "industry_level": "L1",
            "industry_return_method": method,
            "constituent_count": len(values),
            "pct_change": round(pct_change, 2),
            "main_inflow": None,
            "leader_stock": None,
            "turnover_amount": sum(amounts) if amounts else None,
        })
    return rows
