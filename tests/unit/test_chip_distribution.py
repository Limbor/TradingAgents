from __future__ import annotations

import pandas as pd

from tradingagents.core.chip_distribution import compute_chip_profile


def _sample_frame(days: int = 80) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=days)
    close = pd.Series([10 + index * 0.05 for index in range(days)])
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": close - 0.03,
            "High": close + 0.12,
            "Low": close - 0.12,
            "Close": close,
            "Volume": [1_000_000] * days,
            "Turnover": [3.0] * days,
        }
    )


def test_chip_profile_estimates_point_in_time_cost_bands():
    profile = compute_chip_profile(_sample_frame())

    assert profile["status"] == "available"
    assert profile["advisory_only"] is True
    assert profile["sample_days"] == 80
    assert len(profile["series"]) == 61
    current = profile["current"]
    assert current["cost_90_low"] <= current["cost_70_low"]
    assert current["cost_70_low"] <= current["avg_cost"] <= current["cost_70_high"]
    assert current["cost_70_high"] <= current["cost_90_high"]
    assert 0 <= current["profit_ratio"] <= 1
    assert profile["trend"]["note"].startswith("仅用于趋势确认")


def test_chip_profile_refuses_volume_as_turnover_proxy():
    frame = _sample_frame().drop(columns=["Turnover"])

    profile = compute_chip_profile(frame)

    assert profile["status"] == "unavailable"
    assert "换手率" in profile["reason"]


def test_chip_profile_accepts_mcp_candle_field_names():
    frame = _sample_frame().rename(
        columns={
            "Date": "trade_date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Turnover": "turnover_rate",
        }
    )

    profile = compute_chip_profile(frame)

    assert profile["status"] == "available"
    assert profile["current"]["trade_date"] == frame.iloc[-1]["trade_date"].strftime("%Y-%m-%d")
