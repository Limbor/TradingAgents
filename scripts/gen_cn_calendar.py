"""
Generate CN trading calendar cache WITHOUT importing akshare.

This avoids the py_mini_racer / V8 crash on Apple Silicon by fetching
trading calendar data directly from public APIs, then writing the
cache file that cn_trading_calendar.py expects.
"""

import os
import sys
import json
import urllib.request
from datetime import datetime

import pandas as pd

CACHE_DIR = os.path.expanduser("~/.tradingagents/cache")
CACHE_PATH = os.path.join(CACHE_DIR, "cn_trade_cal.csv")


def fetch_from_netse_finance():
    """Fetch SSE Composite Index (000001) daily data from NetEase Finance.
    Every trading day in the result is a CN trading day.
    """
    # NetEase stock history CSV: covers many years
    # Code 0000001 = SSE Composite Index
    url = "http://quotes.money.163.com/service/chddata.html?code=0000001&start=20000101&end=20261231&fields=TCLOSE"
    print(f"Fetching from NetEase Finance: {url[:80]}...")

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()

    # NetEase returns GBK-encoded CSV
    text = raw.decode("gbk", errors="replace")
    from io import StringIO
    df = pd.read_csv(StringIO(text))

    # Column is typically '日期' or the first column
    date_col = df.columns[0]
    dates = pd.to_datetime(df[date_col], errors="coerce").dropna().dt.date.unique()
    dates = sorted(dates)
    result = pd.DataFrame({"trade_date": dates})
    print(f"Got {len(result)} trading days from NetEase Finance")
    return result


def fetch_from_eastmoney():
    """Fetch trading calendar from East Money K-line API."""
    # Use SSE Composite Index (1.000001) daily K-line
    url = (
        "http://push2his.eastmoney.com/api/qt/stock/kline/get?"
        "secid=1.000001&fields1=f1&fields2=f51&klt=101&fqt=0"
        "&beg=20000101&end=20261231"
    )
    print(f"Fetching from East Money API...")

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        raise RuntimeError("No kline data returned")

    # Each kline is like "2024-01-02,3088.87,..."  - we just need the date
    dates = sorted({
        datetime.strptime(k.split(",")[0], "%Y-%m-%d").date()
        for k in klines
    })
    result = pd.DataFrame({"trade_date": dates})
    print(f"Got {len(result)} trading days from East Money")
    return result


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    errors = []
    for name, fetcher in [
        ("East Money", fetch_from_eastmoney),
        ("NetEase Finance", fetch_from_netse_finance),
    ]:
        try:
            df = fetcher()
            if not df.empty:
                df.to_csv(CACHE_PATH, index=False)
                print(f"\nCached {len(df)} trading days to {CACHE_PATH}")
                check = pd.read_csv(CACHE_PATH)
                print(f"Date range: {check['trade_date'].iloc[0]} ~ {check['trade_date'].iloc[-1]}")
                return
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"  {name} failed: {e}")

    print(f"\nAll methods failed: {errors}")
    sys.exit(1)


if __name__ == "__main__":
    main()
