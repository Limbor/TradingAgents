"""Small A-share direction backtest with optional DeepSeek scoring.

Default mode is local-only: it builds the same compact feature payload that
would be sent to DeepSeek, then applies a transparent momentum heuristic. To
call DeepSeek, pass ``--provider deepseek --allow-upload`` after confirming that
uploading the derived market features to a third-party API is acceptable.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

DEFAULT_SAMPLES = {
    "601899.SH": Path.home() / ".tradingagents/cache/601899.SH-TS-data-2021-06-18-2026-06-18.csv",
    "605589.SH": Path.home() / ".tradingagents/cache/605589-AKShare-data-2021-06-17-2026-06-17.csv",
}


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    for col in ("Open", "High", "Low", "Close", "Volume", "Amount", "Turnover"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    close = df["Close"]
    for days in (5, 10, 20, 60):
        df[f"sma{days}"] = close.rolling(days).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi14"] = 100 - 100 / (1 + gain / loss)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macds"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macdh"] = df["macd"] - df["macds"]
    return df.reset_index(drop=True)


def build_payload(symbol: str, csv_path: Path, horizon: int) -> dict:
    df = enrich(pd.read_csv(csv_path))
    if len(df) < max(80, horizon + 25):
        raise ValueError(f"{csv_path} does not contain enough rows")
    idx = len(df) - horizon - 1
    row = df.iloc[idx]
    future = df.iloc[idx + horizon]
    recent = df.iloc[max(0, idx - 20): idx + 1]
    feature = {
        "ret_5d_pct": round((row["Close"] / df.iloc[idx - 5]["Close"] - 1) * 100, 2),
        "ret_10d_pct": round((row["Close"] / df.iloc[idx - 10]["Close"] - 1) * 100, 2),
        "ret_20d_pct": round((row["Close"] / df.iloc[idx - 20]["Close"] - 1) * 100, 2),
        "sma5": round(float(row["sma5"]), 2),
        "sma10": round(float(row["sma10"]), 2),
        "sma20": round(float(row["sma20"]), 2),
        "sma60": round(float(row["sma60"]), 2),
        "rsi14": round(float(row["rsi14"]), 2),
        "macd": round(float(row["macd"]), 4),
        "macds": round(float(row["macds"]), 4),
        "macdh": round(float(row["macdh"]), 4),
    }
    if "Turnover" in df.columns and pd.notna(row.get("Turnover")):
        feature["turnover"] = round(float(row["Turnover"]), 2)
    if "Amount" in df.columns and pd.notna(row.get("Amount")):
        feature["amount"] = round(float(row["Amount"]), 0)

    cols = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume", "Amount", "Turnover", "rsi14", "macdh") if c in recent.columns]
    return {
        "symbol": symbol,
        "signal_date": row["Date"].strftime("%Y-%m-%d"),
        "close": round(float(row["Close"]), 2),
        "future_date": future["Date"].strftime("%Y-%m-%d"),
        "future_return_pct": round((float(future["Close"]) / float(row["Close"]) - 1) * 100, 2),
        "horizon_days": horizon,
        "feature_snapshot": feature,
        "recent_rows": recent.tail(10)[cols]
        .assign(Date=lambda d: d["Date"].dt.strftime("%Y-%m-%d"))
        .round(4)
        .to_dict(orient="records"),
    }


def local_decision(payload: dict) -> dict:
    f = payload["feature_snapshot"]
    trend_up = payload["close"] > f["sma5"] > f["sma10"] > f["sma20"]
    trend_down = payload["close"] < f["sma5"] < f["sma10"] < f["sma20"]
    hot = f["rsi14"] >= 75
    weak = f["rsi14"] <= 35
    if trend_up and not hot and f["macdh"] > 0:
        action = "BUY"
        reason = "Uptrend stack with positive MACD histogram and RSI not yet extremely crowded."
    elif trend_down or weak:
        action = "SELL"
        reason = "Downtrend or weak momentum regime."
    else:
        action = "HOLD"
        reason = "Momentum is extended or mixed; wait for confirmation."
    return {"action": action, "confidence": 0.55, "reason": reason, "risk_controls": "Use A-share T+1 and daily-limit aware stops."}


def build_model_payload(payload: dict) -> dict:
    """Remove forward-looking labels before sending data to a model."""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"future_date", "future_return_pct"}
    }


def deepseek_decision(payload: dict, model: str) -> dict:
    from openai import OpenAI

    load_dotenv(".env")
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    prompt = (
        "你是A股量化/交易复盘助手。只基于给定历史快照，预测未来N个交易日方向。"
        "必须输出JSON，字段: action(BUY/HOLD/SELL), confidence(0-1), reason, risk_controls。"
        "BUY表示预期收益明显为正，SELL表示明显为负，HOLD表示不确定或震荡。\n"
        f"历史快照: {json.dumps(build_model_payload(payload), ensure_ascii=False)}"
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
        timeout=60,
    )
    return json.loads(response.choices[0].message.content)


def is_correct(action: str, future_return_pct: float) -> bool:
    action = action.upper()
    if action == "BUY":
        return future_return_pct > 0
    if action == "SELL":
        return future_return_pct < 0
    return abs(future_return_pct) < 3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["local", "deepseek"], default="local")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--allow-upload", action="store_true")
    args = parser.parse_args()

    if args.provider == "deepseek" and not args.allow_upload:
        raise SystemExit("Refusing DeepSeek upload without --allow-upload.")

    results = []
    for symbol, path in DEFAULT_SAMPLES.items():
        if not path.exists():
            continue
        payload = build_payload(symbol, path, args.horizon)
        decision = (
            deepseek_decision(payload, args.model)
            if args.provider == "deepseek"
            else local_decision(payload)
        )
        results.append(
            {
                "symbol": symbol,
                "signal_date": payload["signal_date"],
                "future_date": payload["future_date"],
                "future_return_pct": payload["future_return_pct"],
                "decision": decision,
                "direction_correct": is_correct(decision.get("action", ""), payload["future_return_pct"]),
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
