"""Lightweight A-share trading calendar.

On first use, fetches the full SSE/SZSE trading-day history via
``akshare.tool_trade_date_hist_sina`` and caches it to a CSV under
``cn_trading_calendar_cache`` (see default_config). Subsequent calls
read from the cache. The calendar is a set of ``datetime.date``
objects, so membership and neighbor lookups are O(1)/O(log n).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Set

import pandas as pd

from .config import get_config

logger = logging.getLogger(__name__)

_calendar_cache: Optional[Set[date]] = None
_sorted_cache: Optional[List[date]] = None


def _to_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return pd.to_datetime(value).date()


def _load_from_disk(path: str) -> Optional[Set[date]]:
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        if df.empty or "trade_date" not in df.columns:
            return None
        days = {pd.to_datetime(d).date() for d in df["trade_date"]}
        return days or None
    except Exception as exc:  # corrupted cache - ignore and refetch
        logger.warning("Failed to read CN trading calendar cache %s: %s", path, exc)
        return None


def _fetch_from_eastmoney() -> Set[date]:
    """Fetch CN trading calendar from East Money K-line API.

    Uses the SSE Composite Index (secid=1.000001) daily K-line data.
    Each returned date is a trading day. This avoids ``py_mini_racer``
    which crashes V8 on Apple Silicon Macs.
    """
    import json
    import urllib.request

    url = (
        "http://push2his.eastmoney.com/api/qt/stock/kline/get?"
        "secid=1.000001&fields1=f1&fields2=f51&klt=101&fqt=0"
        "&beg=20000101&end=20991231"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        raise RuntimeError("East Money API returned no kline data")

    return {
        datetime.strptime(k.split(",")[0], "%Y-%m-%d").date()
        for k in klines
    }


def _fetch_from_akshare() -> Set[date]:
    """Fallback: use akshare (triggers py_mini_racer, may crash on Apple Silicon)."""
    import akshare as ak  # imported lazily; akshare has heavy side-effects

    df = ak.tool_trade_date_hist_sina()
    if df is None or df.empty:
        raise RuntimeError("akshare.tool_trade_date_hist_sina returned no data")
    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    return {pd.to_datetime(d).date() for d in df[col]}


def _persist(days: Iterable[date], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame({"trade_date": sorted(days)}).to_csv(path, index=False)


def _ensure_loaded() -> None:
    global _calendar_cache, _sorted_cache
    if _calendar_cache is not None:
        return

    cache_path = os.path.expanduser(
        get_config().get("cn_trading_calendar_cache", "")
    )

    days = _load_from_disk(cache_path) if cache_path else None

    # Check staleness: refresh if cache's latest date is > 7 days ago
    _STALE_THRESHOLD = 7
    stale = False
    if days is not None:
        latest = max(days)
        if (date.today() - latest).days > _STALE_THRESHOLD:
            logger.info(
                "CN calendar cache is stale (latest=%s, %d days ago), refreshing...",
                latest, (date.today() - latest).days,
            )
            stale = True

    if days is None or stale:
        fresh = None
        for label, fetcher in [
            ("East Money", _fetch_from_eastmoney),
            ("akshare", _fetch_from_akshare),
        ]:
            try:
                logger.info("Fetching CN trading calendar from %s...", label)
                fresh = fetcher()
                break
            except Exception as exc:
                logger.warning("Failed to fetch CN calendar via %s: %s", label, exc)

        if fresh is not None:
            days = fresh
            if cache_path:
                try:
                    _persist(days, cache_path)
                except Exception as exc:
                    logger.warning("Could not persist CN calendar to %s: %s", cache_path, exc)
        elif days is None:
            raise RuntimeError("All CN trading calendar sources failed")
        # else: stale cache is better than nothing — keep using it

    _calendar_cache = days
    _sorted_cache = sorted(days)


def refresh() -> None:
    """Force a re-fetch on next lookup (e.g. after year-end rollover)."""
    global _calendar_cache, _sorted_cache
    _calendar_cache = None
    _sorted_cache = None


def is_trading_day(day) -> bool:
    _ensure_loaded()
    return _to_date(day) in _calendar_cache  # type: ignore[operator]


def prev_trading_day(day) -> date:
    """Return the most recent trading day on or before ``day``."""
    _ensure_loaded()
    d = _to_date(day)
    # linear walk-back is fine: worst case spans a long holiday (~10 days)
    for _ in range(15):
        if d in _calendar_cache:  # type: ignore[operator]
            return d
        d -= timedelta(days=1)
    raise RuntimeError(f"No trading day found within 15 days before {day}")


def next_trading_day(day) -> date:
    _ensure_loaded()
    d = _to_date(day)
    for _ in range(15):
        if d in _calendar_cache:  # type: ignore[operator]
            return d
        d += timedelta(days=1)
    raise RuntimeError(f"No trading day found within 15 days after {day}")


def trading_days_between(start, end) -> List[date]:
    """All trading days in the inclusive range [start, end]."""
    _ensure_loaded()
    s = _to_date(start)
    e = _to_date(end)
    if s > e:
        s, e = e, s
    assert _sorted_cache is not None
    # binary search both bounds
    import bisect
    lo = bisect.bisect_left(_sorted_cache, s)
    hi = bisect.bisect_right(_sorted_cache, e)
    return _sorted_cache[lo:hi]
