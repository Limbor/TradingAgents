from datetime import date, datetime
from zoneinfo import ZoneInfo

import tradingagents.core.trading_time as trading_time
from tradingagents.core.trading_time import get_temporal_context


def test_temporal_context_weekend_keeps_latest_news_cutoff(monkeypatch):
    trading_days = {date(2026, 7, 3), date(2026, 7, 6)}

    monkeypatch.setattr(trading_time, "_is_trading_day", lambda day, market, warnings: day in trading_days)
    monkeypatch.setattr(
        trading_time,
        "_prev_trading_day",
        lambda day, market, warnings: date(2026, 7, 3),
    )
    monkeypatch.setattr(
        trading_time,
        "_next_trading_day",
        lambda day, market, warnings: date(2026, 7, 6),
    )

    context = get_temporal_context(
        {"trading_time_timezone": "Asia/Shanghai"},
        market="cn_a",
        now=datetime(2026, 7, 4, 21, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert context.market_asof_date == "2026-07-03"
    assert context.latest_close_date == "2026-07-03"
    assert context.decision_target_date == "2026-07-06"
    assert context.info_cutoff.startswith("2026-07-04T21:30:00")
    assert context.calendar_state == "holiday"
    assert context.data_policy["news"] == "latest_until_info_cutoff"


def test_temporal_context_historical_pins_info_cutoff(monkeypatch):
    monkeypatch.setattr(trading_time, "_is_trading_day", lambda day, market, warnings: day == date(2026, 7, 3))
    monkeypatch.setattr(trading_time, "_prev_trading_day", lambda day, market, warnings: date(2026, 7, 3))
    monkeypatch.setattr(trading_time, "_next_trading_day", lambda day, market, warnings: date(2026, 7, 6))

    context = get_temporal_context(
        {"trading_time_timezone": "Asia/Shanghai"},
        market="cn_a",
        requested_date="2026-07-04",
        now=datetime(2026, 7, 10, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert context.market_asof_date == "2026-07-03"
    assert context.info_cutoff.startswith("2026-07-04T23:59:59")
    assert context.data_policy["mode"] == "historical"
