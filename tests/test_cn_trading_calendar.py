"""Unit tests for tradingagents.dataflows.cn_trading_calendar.

Avoids any network / akshare call by pointing ``cn_trading_calendar_cache``
at a tmp CSV written per-test.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingagents.dataflows import cn_trading_calendar as cal, config as config_mod


def _write_cache(path, days):
    pd.DataFrame({"trade_date": [d.isoformat() for d in days]}).to_csv(path, index=False)


@pytest.fixture()
def tmp_calendar(tmp_path, monkeypatch):
    """Seed the calendar with a known two-week stretch of trading days.

    Trading days (Mon-Fri, excluding a fake holiday on 2026-05-01):
      2026-04-27 Mon
      2026-04-28 Tue
      2026-04-29 Wed
      2026-04-30 Thu
      (2026-05-01 holiday skipped)
      2026-05-04 Mon
      2026-05-05 Tue
      2026-05-06 Wed
      2026-05-07 Thu
      2026-05-08 Fri
    """
    days = [
        date(2026, 4, 27),
        date(2026, 4, 28),
        date(2026, 4, 29),
        date(2026, 4, 30),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 5, 6),
        date(2026, 5, 7),
        date(2026, 5, 8),
    ]
    cache = tmp_path / "cn_trade_cal.csv"
    _write_cache(cache, days)

    fake_cfg = {"cn_trading_calendar_cache": str(cache)}
    monkeypatch.setattr(config_mod, "get_config", lambda: fake_cfg.copy())
    monkeypatch.setattr(cal, "get_config", lambda: fake_cfg.copy())

    cal.refresh()  # force reload from tmp cache
    yield days
    cal.refresh()  # tear down


@pytest.mark.unit
class TestTradingCalendar:
    def test_is_trading_day_true(self, tmp_calendar):
        assert cal.is_trading_day("2026-04-29") is True
        assert cal.is_trading_day(date(2026, 5, 4)) is True

    def test_is_trading_day_false_weekend(self, tmp_calendar):
        assert cal.is_trading_day("2026-05-02") is False  # Saturday
        assert cal.is_trading_day("2026-05-03") is False  # Sunday

    def test_is_trading_day_false_holiday(self, tmp_calendar):
        assert cal.is_trading_day("2026-05-01") is False

    def test_prev_trading_day_same_day(self, tmp_calendar):
        # April 29 is a trading day -> returns itself
        assert cal.prev_trading_day("2026-04-29") == date(2026, 4, 29)

    def test_prev_trading_day_walks_back_over_holiday(self, tmp_calendar):
        # Weekend + May 1 holiday -> walks back to April 30
        assert cal.prev_trading_day("2026-05-03") == date(2026, 4, 30)
        assert cal.prev_trading_day("2026-05-01") == date(2026, 4, 30)

    def test_next_trading_day_skips_holiday(self, tmp_calendar):
        # May 1 (holiday) -> next trading day is May 4
        assert cal.next_trading_day("2026-05-01") == date(2026, 5, 4)
        # Saturday -> next trading day is Monday
        assert cal.next_trading_day("2026-05-02") == date(2026, 5, 4)

    def test_trading_days_between_inclusive(self, tmp_calendar):
        days = cal.trading_days_between("2026-04-29", "2026-05-05")
        assert days == [
            date(2026, 4, 29),
            date(2026, 4, 30),
            date(2026, 5, 4),
            date(2026, 5, 5),
        ]

    def test_trading_days_between_reversed_inputs(self, tmp_calendar):
        # Swapping start/end should still return ascending range
        a = cal.trading_days_between("2026-05-05", "2026-04-29")
        b = cal.trading_days_between("2026-04-29", "2026-05-05")
        assert a == b

    def test_refresh_reloads_from_disk(self, tmp_path, monkeypatch):
        days = [date(2026, 1, 5), date(2026, 1, 6)]
        cache = tmp_path / "c1.csv"
        _write_cache(cache, days)

        fake_cfg = {"cn_trading_calendar_cache": str(cache)}
        monkeypatch.setattr(cal, "get_config", lambda: fake_cfg.copy())
        cal.refresh()
        assert cal.is_trading_day("2026-01-05") is True
        assert cal.is_trading_day("2026-01-07") is False

        # Rewrite cache with a different day, refresh, and verify.
        _write_cache(cache, [date(2026, 1, 7)])
        cal.refresh()
        assert cal.is_trading_day("2026-01-07") is True
        assert cal.is_trading_day("2026-01-05") is False
