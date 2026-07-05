"""Trading-day temporal context.

The system distinguishes three clocks:

* market_asof_date: latest trading day with usable close data.
* decision_target_date: next tradable date after the market data anchor.
* info_cutoff: latest information timestamp for news/announcements.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo


CalendarState = Literal["trading_day", "holiday"]
SessionState = Literal["before_close_data", "after_close_data", "non_trading"]


@dataclass(frozen=True)
class TradingTemporalContext:
    market: str
    now: str
    timezone: str
    market_asof_date: str
    latest_close_date: str
    decision_target_date: str
    info_cutoff: str
    calendar_state: CalendarState
    session_state: SessionState
    source: str
    warnings: list[str]
    data_policy: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_temporal_context(
    config: dict[str, Any] | None = None,
    *,
    market: str = "cn_a",
    mode: str = "current",
    requested_date: str | date | None = None,
    now: datetime | None = None,
    require_close: bool = True,
) -> TradingTemporalContext:
    """Build the temporal context used by skills and API callers.

    ``current`` mode allows news and announcements up to ``now``. When a
    historical ``requested_date`` is supplied, ``info_cutoff`` is pinned to the
    end of that date so backtests do not see current news.
    """

    cfg = config or {}
    market = (market or "cn_a").lower()
    tz_name = str(cfg.get("trading_time_timezone") or "Asia/Shanghai")
    tz = ZoneInfo(tz_name)
    now_dt = now.astimezone(tz) if now else datetime.now(tz)
    warnings: list[str] = []
    source = "calendar"

    if requested_date is not None:
        req = _to_date(requested_date)
        market_asof = _prev_trading_day(req, market, warnings)
        info_cutoff = datetime.combine(req, time(23, 59, 59), tzinfo=tz)
        calendar_state: CalendarState = "trading_day" if _is_trading_day(req, market, warnings) else "holiday"
        session_state: SessionState = "after_close_data" if calendar_state == "trading_day" else "non_trading"
        mode = "historical"
    else:
        today = now_dt.date()
        is_trading = _is_trading_day(today, market, warnings)
        calendar_state = "trading_day" if is_trading else "holiday"
        close_available_at = _close_available_time(cfg, market)
        if is_trading and (not require_close or now_dt.time() >= close_available_at):
            market_asof = today
            session_state = "after_close_data"
        elif is_trading:
            market_asof = _prev_trading_day(today - timedelta(days=1), market, warnings)
            session_state = "before_close_data"
        else:
            market_asof = _prev_trading_day(today, market, warnings)
            session_state = "non_trading"
        info_cutoff = now_dt

    decision_target = _next_trading_day(market_asof + timedelta(days=1), market, warnings)
    return TradingTemporalContext(
        market=market,
        now=now_dt.isoformat(),
        timezone=tz_name,
        market_asof_date=market_asof.isoformat(),
        latest_close_date=market_asof.isoformat(),
        decision_target_date=decision_target.isoformat(),
        info_cutoff=info_cutoff.isoformat(),
        calendar_state=calendar_state,
        session_state=session_state,
        source=source if not warnings else "calendar_with_fallback",
        warnings=warnings,
        data_policy={
            "price": "asof_market_close",
            "factor": "asof_market_close",
            "fundamental": "announced_before_info_cutoff",
            "news": "latest_until_info_cutoff",
            "announcement": "latest_until_info_cutoff",
            "flow": "best_effort_with_source_lag",
            "decision": "target_next_trading_day",
            "mode": mode,
        },
    )


def get_market_asof_date(config: dict[str, Any] | None = None, *, market: str = "cn_a") -> str:
    return get_temporal_context(config, market=market).market_asof_date


def get_info_cutoff_date(config: dict[str, Any] | None = None, *, market: str = "cn_a") -> date:
    context = get_temporal_context(config, market=market)
    return datetime.fromisoformat(context.info_cutoff).date()


def advance_trading_days(start: str | date, days: int, market: str = "cn_a") -> str | None:
    """Return the ISO date ``days`` trading days after ``start``.

    Aligned with the reflection engine's trading-day-based horizon: a case
    created on ``signal_date`` with ``horizon_days=5`` is due for reflection
    on ``advance_trading_days(signal_date, 5)``. Returns None if the trading
    calendar is unavailable (the frontend then falls back to a natural-day
    countdown).
    """
    if days <= 0:
        try:
            return _to_date(start).isoformat()
        except Exception:
            return None
    warnings: list[str] = []
    current = _to_date(start)
    for _ in range(int(days)):
        current = _next_trading_day(current + timedelta(days=1), market, warnings)
    return current.isoformat()


def _close_available_time(config: dict[str, Any], market: str) -> time:
    key = f"{market}_close_data_available_time"
    raw = str(config.get(key) or config.get("close_data_available_time") or "15:30")
    try:
        hour, minute, *_ = [int(part) for part in raw.split(":")]
        return time(hour, minute)
    except Exception:
        return time(15, 30)


def _is_trading_day(day: date, market: str, warnings: list[str]) -> bool:
    if market == "cn_a":
        try:
            from tradingagents.dataflows.cn_trading_calendar import is_trading_day

            return bool(is_trading_day(day))
        except Exception as exc:
            warnings.append(f"CN trading calendar unavailable; weekday fallback used: {exc}")
    return day.weekday() < 5


def _prev_trading_day(day: date, market: str, warnings: list[str]) -> date:
    if market == "cn_a":
        try:
            from tradingagents.dataflows.cn_trading_calendar import prev_trading_day

            return prev_trading_day(day)
        except Exception as exc:
            warnings.append(f"CN previous trading day fallback used: {exc}")
    current = day
    for _ in range(15):
        if current.weekday() < 5:
            return current
        current -= timedelta(days=1)
    return day


def _next_trading_day(day: date, market: str, warnings: list[str]) -> date:
    if market == "cn_a":
        try:
            from tradingagents.dataflows.cn_trading_calendar import next_trading_day

            return next_trading_day(day)
        except Exception as exc:
            warnings.append(f"CN next trading day fallback used: {exc}")
    current = day
    for _ in range(15):
        if current.weekday() < 5:
            return current
        current += timedelta(days=1)
    return day


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
