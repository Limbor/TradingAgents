"""Historical signal fusion backtest.

Replays the daily pipeline for past trade dates, compares generated signals
against actual N-day forward returns, computes hit-rate and risk metrics.

Usage:
    # quant-only mode (fast, no LLM token cost)
    python scripts/backtest_signal_fusion.py \
        --start-date 2026-05-01 --end-date 2026-06-20 \
        --horizon 5 --universe 000906.SH --style medium_term

    # quant+LLM fusion mode (uses LLM tokens)
    python scripts/backtest_signal_fusion.py \
        --start-date 2026-06-01 --end-date 2026-06-20 \
        --horizon 5 --mode fused

Requirements:
    - StockManager MCP running (for rank_factor_candidates and get_stock_daily)
    - Or AKShare fallback for forward-return data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.core.mcp_client import StockManagerMCPClient, config_from_app_config
from tradingagents.core.signal_fusion import fuse_candidate_signal
from tradingagents.dataflows.mcp_adapter import (
    normalize_quant_candidate,
    payload_rows,
)
from tradingagents.default_config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def is_signal_correct(signal: str, forward_return_pct: float) -> bool:
    """Evaluate if a signal's direction matches actual forward return.

    BUY → return > 0%
    WATCHLIST/HOLD → |return| < 3%
    AVOID → return < 0%
    """
    signal = signal.upper()
    if signal == "BUY":
        return forward_return_pct > 0
    if signal == "AVOID":
        return forward_return_pct < 0
    # WATCHLIST / HOLD → correct if market stayed flat-ish
    return abs(forward_return_pct) < 3.0


def compute_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute backtest performance metrics from signal results."""
    if not results:
        return {"error": "no results"}

    total = len(results)
    correct = sum(1 for r in results if r["direction_correct"])
    buy_results = [r for r in results if r["signal"] == "BUY"]
    avoid_results = [r for r in results if r["signal"] == "AVOID"]
    watchlist_results = [r for r in results if r["signal"] in ("WATCHLIST", "HOLD")]

    buy_correct = sum(1 for r in buy_results if r["direction_correct"])
    buy_returns = [r["forward_return_pct"] for r in buy_results]

    metrics = {
        "total_signals": total,
        "overall_hit_rate": round(correct / total * 100, 1) if total else 0,
        "signal_distribution": {
            "BUY": len(buy_results),
            "WATCHLIST": len(watchlist_results),
            "AVOID": len(avoid_results),
        },
        "buy_hit_rate": round(buy_correct / len(buy_results) * 100, 1) if buy_results else None,
        "avg_buy_return": round(sum(buy_returns) / len(buy_returns), 2) if buy_returns else None,
        "max_buy_loss": round(min(buy_returns), 2) if buy_returns else None,
        "max_buy_gain": round(max(buy_returns), 2) if buy_returns else None,
    }

    # Simplified Sharpe approximation for BUY signals
    if len(buy_returns) >= 2:
        import statistics
        mean_ret = statistics.mean(buy_returns)
        std_ret = statistics.stdev(buy_returns)
        metrics["sharpe_approx"] = round(mean_ret / std_ret, 2) if std_ret > 0 else None
    else:
        metrics["sharpe_approx"] = None

    return metrics


def _factor_profile_for_style(style: str) -> str:
    if style == "short_term":
        return "short_term_momentum"
    if style == "long_term":
        return "long_term_quality"
    return "medium_term_balanced"


def _default_filters() -> dict[str, Any]:
    return {
        "exclude_st": True,
        "exclude_suspended": True,
        "exclude_one_price_limit": True,
        "min_amount_20d": 0,
    }


async def get_forward_return(
    client: StockManagerMCPClient | None,
    symbol: str,
    signal_date: str,
    horizon: int,
) -> float | None:
    """Get N-day forward return for a symbol from signal_date.

    Tries MCP first, falls back to AKShare.
    """
    try:
        td = datetime.strptime(signal_date, "%Y-%m-%d")
        end_date = (td + timedelta(days=horizon + 10)).strftime("%Y%m%d")
        start_date = td.strftime("%Y%m%d")

        if client is not None:
            payload = await client.get_stock_daily(
                ts_codes=[symbol],
                start_date=start_date,
                end_date=end_date,
                adj_type="qfq",
            )
            if payload and isinstance(payload, dict):
                rows = payload.get("rows") or payload.get("data", {}).get("rows") or []
                if len(rows) > horizon:
                    # rows should be sorted by date
                    close_start = float(rows[0].get("close", 0))
                    close_end = float(rows[min(horizon, len(rows) - 1)].get("close", 0))
                    if close_start > 0:
                        return round((close_end / close_start - 1) * 100, 2)
    except Exception as exc:
        logger.debug("Forward return fetch failed for %s: %s", symbol, exc)

    # Fallback: try AKShare
    try:
        from tradingagents.dataflows.akshare_stock import get_stock
        td = datetime.strptime(signal_date, "%Y-%m-%d")
        end_dt = td + timedelta(days=horizon + 10)
        raw = get_stock(symbol, signal_date, end_dt.strftime("%Y-%m-%d"))
        if raw and not raw.startswith("Error"):
            import csv
            import io
            reader = csv.DictReader(io.StringIO(raw))
            rows = list(reader)
            if len(rows) > horizon:
                close_start = float(rows[0].get("Close") or rows[0].get("close") or 0)
                close_end = float(rows[min(horizon, len(rows) - 1)].get("Close") or rows[min(horizon, len(rows) - 1)].get("close") or 0)
                if close_start > 0:
                    return round((close_end / close_start - 1) * 100, 2)
    except Exception as exc:
        logger.debug("AKShare forward return fallback failed for %s: %s", symbol, exc)

    return None


async def run_backtest(
    start_date: str,
    end_date: str,
    horizon: int,
    universe: str,
    style: str,
    mode: str = "quant_only",
    limit: int = 5,
    candidate_limit: int = 80,
) -> list[dict[str, Any]]:
    """Run the historical backtest over a date range.

    For each trade_date:
      1. Call MCP rank_factor_candidates (real historical ranking)
      2. Fuse signal (quant_only or quant_llm_fused depending on mode)
      3. Record: symbol, signal, final_score
      4. Fetch actual N-day forward return
      5. Compute direction correctness
    """
    config = dict(DEFAULT_CONFIG)
    mcp_config = config_from_app_config(config)
    client = StockManagerMCPClient(mcp_config)

    try:
        connected = await client.connect()
        if not connected:
            logger.error("Cannot connect to StockManager MCP. Backtest requires MCP.")
            return []
    except Exception as exc:
        logger.error("MCP connection failed: %s", exc)
        return []

    # Generate trade dates
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    trade_dates = []
    current = start
    while current <= end:
        # Skip weekends
        if current.weekday() < 5:
            trade_dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    results = []
    for i, trade_date in enumerate(trade_dates):
        logger.info("Processing %s (%d/%d)", trade_date, i + 1, len(trade_dates))

        try:
            payload = await client.rank_factor_candidates(
                universe_index=universe,
                trade_date=trade_date,
                style=style,
                limit=limit,
                candidate_limit=candidate_limit,
                factor_profile=_factor_profile_for_style(style),
                filters=_default_filters(),
                sector_prefs=[],
                return_factor_snapshot=True,
            )
        except Exception as exc:
            logger.warning("rank_factor_candidates failed for %s: %s", trade_date, exc)
            continue

        rows = payload_rows(payload)
        if not rows:
            logger.info("No candidates for %s", trade_date)
            continue

        for row in rows[:limit]:
            candidate = normalize_quant_candidate(row)
            fusion = fuse_candidate_signal(candidate, style)
            candidate.update(fusion)

            symbol = candidate["symbol"]
            signal = candidate["signal"]
            final_score = candidate["final_score"]

            # Get forward return
            forward_return = await get_forward_return(client, symbol, trade_date, horizon)
            if forward_return is None:
                logger.debug("No forward return for %s on %s", symbol, trade_date)
                continue

            results.append({
                "trade_date": trade_date,
                "symbol": symbol,
                "name": candidate.get("name", ""),
                "signal": signal,
                "final_score": final_score,
                "quant_score": candidate.get("quant_score"),
                "forward_return_pct": forward_return,
                "direction_correct": is_signal_correct(signal, forward_return),
                "horizon": horizon,
                "fusion_mode": candidate.get("fusion_mode", "quant_only"),
            })

    await client.disconnect()
    return results


def print_results(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    """Pretty-print backtest results."""
    metrics = compute_metrics(results)

    print("\n═══════════════════════════════════════════")
    print(" Signal Fusion Backtest Results")
    print("═══════════════════════════════════════════")
    print(f" Period: {args.start_date} → {args.end_date}")
    print(f" Horizon: {args.horizon} days forward")
    print(f" Universe: {args.universe}")
    print(f" Style: {args.style}")
    print(f" Mode: {args.mode}")
    print("───────────────────────────────────────────")
    print(f" Total signals: {metrics['total_signals']}")

    dist = metrics.get("signal_distribution", {})
    buy_count = dist.get("BUY", 0)
    buy_hr = metrics.get("buy_hit_rate")
    print(f" BUY signals: {buy_count} (hit rate: {buy_hr}%)" if buy_hr else f" BUY signals: {buy_count}")
    if metrics.get("avg_buy_return") is not None:
        print(f" Avg BUY return: {metrics['avg_buy_return']:+.2f}%")
    if metrics.get("max_buy_loss") is not None:
        print(f" Max BUY loss: {metrics['max_buy_loss']:+.2f}%")
    if metrics.get("max_buy_gain") is not None:
        print(f" Max BUY gain: {metrics['max_buy_gain']:+.2f}%")
    if metrics.get("sharpe_approx") is not None:
        print(f" Sharpe (approx): {metrics['sharpe_approx']}")

    print(f" WATCHLIST signals: {dist.get('WATCHLIST', 0)}")
    print(f" AVOID signals: {dist.get('AVOID', 0)}")
    print(f" Overall direction accuracy: {metrics['overall_hit_rate']}%")
    print("═══════════════════════════════════════════\n")

    # Save detailed results to JSON
    output_path = Path(__file__).parent / f"backtest_results_{args.start_date}_{args.end_date}.json"
    output_data = {
        "args": vars(args),
        "metrics": metrics,
        "signals": results,
    }
    output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2))
    print(f" Results saved to: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal fusion historical backtest")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--horizon", type=int, default=5, help="Forward return horizon in trading days")
    parser.add_argument("--universe", default="000906.SH", help="Universe index (default CSI800)")
    parser.add_argument("--style", default="medium_term", choices=["short_term", "medium_term", "long_term"])
    parser.add_argument("--mode", default="quant_only", choices=["quant_only", "fused"])
    parser.add_argument("--limit", type=int, default=5, help="Top N candidates per day")
    parser.add_argument("--candidate-limit", type=int, default=80, help="MCP candidate pool size")
    args = parser.parse_args()

    results = asyncio.run(run_backtest(
        start_date=args.start_date,
        end_date=args.end_date,
        horizon=args.horizon,
        universe=args.universe,
        style=args.style,
        mode=args.mode,
        limit=args.limit,
        candidate_limit=args.candidate_limit,
    ))

    if not results:
        print("\n⚠️  No results generated. Check MCP connection and date range.")
        sys.exit(1)

    print_results(results, args)


if __name__ == "__main__":
    main()
