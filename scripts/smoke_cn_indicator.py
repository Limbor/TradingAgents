"""Smoke test for the A-share indicator data flow.

Tests the full chain:
  1. TuShare connection + pro_bar fetch
  2. AKShare connection + stock_zh_a_hist fetch
  3. akshare_stock.get_indicator (vendor-direct)
  4. tushare_stock.get_indicator (vendor-direct)
  5. interface.route_to_vendor("get_indicators", ...) (full route)
  6. Non-trading-day auto-adjust verification

Each step prints a one-line summary and the first 200 chars of the result.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import date

SYMBOL = "600158.SH"  # 大唐发电
INDICATOR = "close_50_sma"
CURR_DATE = "2026-04-29"  # weekday today
LOOK_BACK = 30


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def _show(name: str, result) -> None:
    if isinstance(result, str):
        first = result[:300].replace("\n", " | ")
        print(f"[OK] {name}: {first}{'...' if len(result) > 300 else ''}")
    else:
        print(f"[OK] {name}: type={type(result).__name__} repr={repr(result)[:200]}")


def step_env() -> None:
    _banner("STEP 1: Environment")
    token = os.environ.get("TUSHARE_TOKEN", "")
    print(f"TUSHARE_TOKEN: {'set' if token else 'MISSING'} (len={len(token)})")
    print(f"Python: {sys.version.split()[0]}")


def step_tushare_raw() -> None:
    _banner("STEP 2: TuShare raw pro_bar")
    # Note: ts.pro_bar is a top-level helper, NOT a method on pro_api()
    import tushare as ts
    from tradingagents.dataflows.tushare_common import get_pro_api, tushare_call
    get_pro_api()  # ensure ts.set_token(...) was called
    df = tushare_call(
        ts.pro_bar,
        ts_code="600158.SH",
        adj="qfq",
        start_date="20260101",
        end_date="20260429",
    )
    print(f"DataFrame shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    if not df.empty:
        print(df.head(3).to_string())


def step_akshare_raw() -> None:
    _banner("STEP 3: AKShare raw stock_zh_a_hist")
    from tradingagents.dataflows.akshare_common import akshare_call, ak_lazy_import
    ak = ak_lazy_import()
    df = akshare_call(
        ak.stock_zh_a_hist,
        symbol="600158",
        period="daily",
        start_date="20260101",
        end_date="20260429",
        adjust="qfq",
    )
    print(f"DataFrame shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    if not df.empty:
        print(df.head(3).to_string())


def step_akshare_indicator() -> None:
    _banner("STEP 4: akshare_stock.get_indicator (direct)")
    from tradingagents.dataflows.akshare_stock import get_indicator
    out = get_indicator(SYMBOL, INDICATOR, CURR_DATE, LOOK_BACK)
    _show("akshare get_indicator", out)


def step_tushare_indicator() -> None:
    _banner("STEP 5: tushare_stock.get_indicator (direct)")
    from tradingagents.dataflows.tushare_stock import get_indicator
    out = get_indicator(SYMBOL, INDICATOR, CURR_DATE, LOOK_BACK)
    _show("tushare get_indicator", out)


def step_route() -> None:
    _banner("STEP 6: interface.route_to_vendor get_indicators")
    from tradingagents.dataflows.interface import route_to_vendor
    out = route_to_vendor("get_indicators", SYMBOL, INDICATOR, CURR_DATE, LOOK_BACK)
    _show("route_to_vendor get_indicators", out)


def step_non_trading_day() -> None:
    _banner("STEP 7: Non-trading-day auto-adjust")
    # Saturday (use a Saturday in 2026)
    sat = "2026-04-25"  # 2026-04-25 is a Saturday
    from datetime import date as _date
    real = _date.fromisoformat(sat)
    print(f"Input date: {sat} (weekday={real.strftime('%A')})")
    from tradingagents.dataflows.cn_trading_calendar import (
        is_trading_day,
        prev_trading_day,
    )
    print(f"is_trading_day: {is_trading_day(sat)}")
    print(f"prev_trading_day: {prev_trading_day(sat)}")

    # Use the full router so AKShare network failures fall back to TuShare,
    # demonstrating BOTH non-trading-day adjustment AND the vendor fallback.
    from tradingagents.dataflows.interface import route_to_vendor
    out = route_to_vendor("get_indicators", SYMBOL, INDICATOR, sat, LOOK_BACK)
    print(f"Output starts with: {out[:200]}")


def main() -> int:
    rc = 0
    steps = [
        ("env", step_env),
        ("tushare_raw", step_tushare_raw),
        ("akshare_raw", step_akshare_raw),
        ("akshare_indicator", step_akshare_indicator),
        ("tushare_indicator", step_tushare_indicator),
        ("route", step_route),
        ("non_trading_day", step_non_trading_day),
    ]
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:
            print(f"\n[FAIL] step {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            rc = 1
    print()
    print("=" * 60)
    print(f"  RESULT: {'ALL OK' if rc == 0 else 'FAILURES (see above)'}")
    print("=" * 60)
    return rc


if __name__ == "__main__":
    sys.exit(main())
