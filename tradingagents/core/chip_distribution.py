"""Deterministic A-share chip-cost distribution estimation.

The estimator uses OHLC and daily turnover to model how outstanding chips
migrate between price buckets.  It is an *estimated cost distribution*, not
shareholder-level position data, and therefore only supplies advisory evidence
to the pre-trade review.  It must not independently make a trade executable.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

MIN_SAMPLE_DAYS = 20
DEFAULT_WINDOW = 120
DEFAULT_BINS = 150


def unavailable_chip_profile(reason: str) -> dict[str, Any]:
    """Return the stable API shape used when required source data is missing."""
    return {
        "status": "unavailable",
        "method": "turnover_decay_triangular_v1",
        "advisory_only": True,
        "reason": reason,
        "sample_days": 0,
        "current": None,
        "trend": None,
        "series": [],
    }


def compute_chip_profile(
    frame: pd.DataFrame | None,
    *,
    window: int = DEFAULT_WINDOW,
    bins: int = DEFAULT_BINS,
) -> dict[str, Any]:
    """Estimate rolling chip-cost bands from canonical OHLC + turnover data.

    ``Turnover``/``turnover_rate`` is expected in percentage points (for
    example ``2.5`` means 2.5%).  The implementation intentionally works from
    caller-provided point-in-time rows, so no future bars are fetched here.
    """
    if frame is None or frame.empty:
        return unavailable_chip_profile("没有可用于筹码估算的行情数据")
    if window < MIN_SAMPLE_DAYS or bins < 20:
        return unavailable_chip_profile("筹码估算参数无效")

    data = _normalise_frame(frame)
    if data is None:
        return unavailable_chip_profile("行情缺少 OHLC 或换手率，无法估算筹码分布")
    if len(data) < MIN_SAMPLE_DAYS:
        return unavailable_chip_profile(f"有效样本不足 {MIN_SAMPLE_DAYS} 个交易日")

    turnover_coverage = float(data["turnover"].notna().mean())
    if turnover_coverage < 0.8:
        return unavailable_chip_profile(
            f"换手率覆盖率仅 {turnover_coverage:.0%}，不足以可靠估算筹码分布"
        )
    data = data.dropna(subset=["turnover"]).tail(max(window * 2, window)).reset_index(drop=True)
    series: list[dict[str, Any]] = []
    first_index = min(MIN_SAMPLE_DAYS - 1, len(data) - 1)
    for index in range(first_index, len(data)):
        sample = data.iloc[max(0, index - window + 1) : index + 1]
        point = _estimate_point(sample, bins=bins)
        if point is None:
            continue
        point["trade_date"] = data.iloc[index]["date"].strftime("%Y-%m-%d")
        series.append(point)

    if not series:
        return unavailable_chip_profile("筹码分布计算未产生有效结果")

    trend = _classify_trend(series)
    return {
        "status": "available",
        "method": "turnover_decay_triangular_v1",
        "advisory_only": True,
        "reason": "基于历史 OHLC 与换手率的概率估算，并非真实股东持仓",
        "sample_days": min(len(data), window),
        "turnover_coverage": round(turnover_coverage, 4),
        "current": series[-1],
        "trend": trend,
        "series": series[-window:],
    }


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame | None:
    aliases = {
        "date": ("Date", "date", "trade_date", "index"),
        "open": ("Open", "open"),
        "high": ("High", "high"),
        "low": ("Low", "low"),
        "close": ("Close", "close"),
        "turnover": ("Turnover", "turnover", "turnover_rate", "hsl"),
    }
    selected: dict[str, Any] = {}
    for target, candidates in aliases.items():
        source = next((name for name in candidates if name in frame.columns), None)
        if source is None:
            return None
        selected[target] = frame[source]
    data = pd.DataFrame(selected)
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for column in ("open", "high", "low", "close", "turnover"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["date", "open", "high", "low", "close"])
    data = data[(data[["open", "high", "low", "close"]] > 0).all(axis=1)]
    data["turnover"] = data["turnover"].clip(lower=0, upper=100)
    return data.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _estimate_point(sample: pd.DataFrame, *, bins: int) -> dict[str, Any] | None:
    min_price = float(sample["low"].min())
    max_price = float(sample["high"].max())
    if not np.isfinite(min_price) or not np.isfinite(max_price) or max_price <= 0:
        return None
    accuracy = max(0.01, (max_price - min_price) / (bins - 1))
    prices = min_price + accuracy * np.arange(bins, dtype=float)
    chips = np.zeros(bins, dtype=float)

    for row in sample.itertuples(index=False):
        high = float(row.high)
        low = float(row.low)
        average = (float(row.open) + float(row.close) + high + low) / 4.0
        turnover = min(1.0, max(0.0, float(row.turnover) / 100.0))
        chips *= 1.0 - turnover
        if turnover <= 0:
            continue

        if abs(high - low) < 1e-12:
            bucket = int(np.clip(np.floor((average - min_price) / accuracy), 0, bins - 1))
            chips[bucket] += (bins - 1) * turnover / 2.0
            continue

        low_bucket = int(np.clip(np.ceil((low - min_price) / accuracy), 0, bins - 1))
        high_bucket = int(np.clip(np.floor((high - min_price) / accuracy), 0, bins - 1))
        density = 2.0 / (high - low)
        for bucket in range(low_bucket, high_bucket + 1):
            price = prices[bucket]
            if price <= average:
                denominator = average - low
                weight = density if abs(denominator) < 1e-12 else (price - low) / denominator * density
            else:
                denominator = high - average
                weight = density if abs(denominator) < 1e-12 else (high - price) / denominator * density
            chips[bucket] += max(0.0, weight) * turnover

    total = float(chips.sum())
    if total <= 0 or not np.isfinite(total):
        return None
    current_price = float(sample.iloc[-1]["close"])
    cumulative = np.cumsum(chips)

    def quantile_price(quantile: float) -> float:
        bucket = int(np.searchsorted(cumulative, total * quantile, side="right"))
        return float(prices[min(bucket, bins - 1)])

    low_90, high_90 = quantile_price(0.05), quantile_price(0.95)
    low_70, high_70 = quantile_price(0.15), quantile_price(0.85)
    profit_ratio = float(chips[prices <= current_price].sum() / total)
    median_cost = quantile_price(0.5)
    return {
        "close": round(current_price, 4),
        "avg_cost": round(median_cost, 4),
        "profit_ratio": round(profit_ratio, 4),
        "cost_70_low": round(low_70, 4),
        "cost_70_high": round(high_70, 4),
        "concentration_70": round(_concentration(low_70, high_70), 6),
        "cost_90_low": round(low_90, 4),
        "cost_90_high": round(high_90, 4),
        "concentration_90": round(_concentration(low_90, high_90), 6),
    }


def _concentration(low: float, high: float) -> float:
    denominator = low + high
    return 0.0 if denominator == 0 else (high - low) / denominator


def _classify_trend(series: list[dict[str, Any]]) -> dict[str, Any]:
    latest = series[-1]
    comparison = series[max(0, len(series) - 6)]
    avg_cost = float(latest["avg_cost"])
    close = float(latest["close"])
    base_cost = float(comparison["avg_cost"])
    cost_slope = 0.0 if base_cost == 0 else (avg_cost / base_cost - 1.0) * 100.0
    concentration_change = float(latest["concentration_70"]) - float(comparison["concentration_70"])
    distance = 0.0 if avg_cost == 0 else (close / avg_cost - 1.0) * 100.0
    profit_ratio = float(latest["profit_ratio"])
    score = 50
    reasons: list[str] = []
    risks: list[str] = []

    if close > avg_cost:
        score += 15
        reasons.append("现价位于估算平均成本上方")
    else:
        score -= 15
        risks.append("现价尚未站上估算平均成本")
    if close > float(latest["cost_70_high"]):
        score += 15
        reasons.append("现价突破 70% 筹码成本上沿")
    elif close < float(latest["cost_70_low"]):
        score -= 20
        risks.append("现价跌破 70% 筹码成本下沿")
    if cost_slope > 0.5:
        score += 10
        reasons.append("近 5 日成本重心上移")
    elif cost_slope < -0.5:
        score -= 10
        risks.append("近 5 日成本重心下移")
    if concentration_change < -0.01:
        score += 10
        reasons.append("近 5 日筹码趋于集中")
    elif concentration_change > 0.01:
        score -= 5
        risks.append("近 5 日筹码趋于发散")
    if 0.55 <= profit_ratio <= 0.9:
        score += 5
        reasons.append("获利盘比例处于趋势确认区间")
    if profit_ratio >= 0.95 and distance >= 12:
        score -= 10
        risks.append("获利盘拥挤且现价显著偏离成本，存在兑现压力")

    score = max(0, min(100, score))
    if profit_ratio >= 0.95 and distance >= 12:
        state = "crowded"
    elif score >= 70:
        state = "bullish_confirmed"
    elif score <= 35:
        state = "weakening"
    elif cost_slope > 0:
        state = "improving"
    else:
        state = "neutral"
    return {
        "state": state,
        "confirmation_score": score,
        "avg_cost_slope_5d_pct": round(cost_slope, 2),
        "price_vs_avg_cost_pct": round(distance, 2),
        "concentration_change_5d": round(concentration_change, 6),
        "reasons": reasons,
        "risks": risks,
        "note": "仅用于趋势确认和风险提示，不独立构成买卖建议",
    }
