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

from tradingagents.core.llm_candidate_review import build_candidate_reviewer
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
    SKIP/AVOID/HOLD_REVIEW → return <= 0%
    """
    signal = signal.upper()
    if signal == "BUY":
        return forward_return_pct > 0
    if signal in {"SKIP", "AVOID", "HOLD_REVIEW", "SELL"}:
        return forward_return_pct <= 0
    # WATCHLIST / MONITOR / HOLD → correct if market stayed flat-ish
    return abs(forward_return_pct) < 3.0


def compute_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute backtest performance metrics from signal results."""
    if not results:
        return {"error": "no results"}

    total = len(results)
    correct = sum(1 for r in results if r["direction_correct"])
    buy_results = [r for r in results if r["signal"] == "BUY"]

    buy_correct = sum(1 for r in buy_results if r["direction_correct"])
    buy_returns = [r["forward_return_pct"] for r in buy_results]

    metrics = {
        "total_signals": total,
        "overall_hit_rate": round(correct / total * 100, 1) if total else 0,
        "signal_distribution": {
            signal: sum(1 for r in results if r["signal"] == signal)
            for signal in sorted({r["signal"] for r in results})
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


def compute_comparison_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Paired champion/challenger metrics over identical date-symbol samples."""
    variants = {
        variant: compute_metrics([r for r in results if r.get("variant") == variant])
        for variant in ("quant_only", "fused")
    }
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in results:
        key = (row["trade_date"], row["symbol"], int(row["horizon"]))
        grouped.setdefault(key, {})[str(row.get("variant"))] = row
    pairs = [rows for rows in grouped.values() if {"quant_only", "fused"} <= set(rows)]
    improved = sum(
        1 for rows in pairs
        if not rows["quant_only"]["direction_correct"] and rows["fused"]["direction_correct"]
    )
    degraded = sum(
        1 for rows in pairs
        if rows["quant_only"]["direction_correct"] and not rows["fused"]["direction_correct"]
    )
    delta = (
        100.0 * sum(
            int(rows["fused"]["direction_correct"])
            - int(rows["quant_only"]["direction_correct"])
            for rows in pairs
        ) / len(pairs)
        if pairs else None
    )
    return {
        "variants": variants,
        "paired_count": len(pairs),
        "fused_improved": improved,
        "fused_degraded": degraded,
        "paired_accuracy_delta_pp": round(delta, 2) if delta is not None else None,
    }


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


async def fuse_for_backtest(
    candidate: dict[str, Any],
    *,
    style: str,
    mode: str,
    reviewer: Any | None = None,
) -> dict[str, Any]:
    """Fuse one historical candidate, failing closed in requested LLM mode."""
    if mode == "quant_only":
        return fuse_candidate_signal(candidate, style)
    if mode != "fused":
        raise ValueError(f"Unsupported backtest mode: {mode}")
    if reviewer is None:
        raise RuntimeError("fused backtest requested but LLM reviewer is unavailable")
    review = await reviewer.review(candidate)
    payload = review.as_fusion_payload() if hasattr(review, "as_fusion_payload") else review
    if not isinstance(payload, dict):
        raise TypeError("LLM reviewer returned an unsupported fusion payload")
    return fuse_candidate_signal(candidate, style, payload)


def calculate_net_forward_return(
    rows: list[dict[str, Any]],
    *,
    signal_date: str,
    horizon: int,
    transaction_cost_bps: float,
    slippage_bps: float,
) -> float | None:
    """Next-session-open to horizon-close return after round-trip costs."""
    if horizon < 1 or not rows:
        return None

    def _date(row: dict[str, Any]) -> str:
        return str(
            row.get("trade_date") or row.get("date") or row.get("Date") or ""
        ).replace("-", "")

    ordered = sorted(rows, key=_date)
    cutoff = signal_date.replace("-", "")
    entry_idx = next((i for i, row in enumerate(ordered) if _date(row) > cutoff), None)
    if entry_idx is None:
        return None
    exit_idx = entry_idx + horizon - 1
    if exit_idx >= len(ordered):
        return None

    entry_row = ordered[entry_idx]
    exit_row = ordered[exit_idx]
    try:
        entry = float(
            entry_row.get("open") or entry_row.get("Open")
            or entry_row.get("close") or entry_row.get("Close") or 0
        )
        exit_price = float(exit_row.get("close") or exit_row.get("Close") or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or exit_price <= 0:
        return None
    side_cost = max(0.0, float(transaction_cost_bps) + float(slippage_bps)) / 10_000
    net = exit_price * (1.0 - side_cost) / (entry * (1.0 + side_cost)) - 1.0
    return round(net * 100, 4)


async def get_forward_return(
    client: StockManagerMCPClient | None,
    symbol: str,
    signal_date: str,
    horizon: int,
    transaction_cost_bps: float = 10.0,
    slippage_bps: float = 5.0,
) -> float | None:
    """Get N-day forward return for a symbol from signal_date.

    Tries MCP first, falls back to AKShare.
    """
    try:
        td = datetime.strptime(signal_date, "%Y-%m-%d")
        end_date = (td + timedelta(days=horizon * 2 + 10)).strftime("%Y%m%d")
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
                if isinstance(rows, dict):
                    rows = rows.get(symbol) or rows.get(symbol.upper()) or []
                value = calculate_net_forward_return(
                    rows,
                    signal_date=signal_date,
                    horizon=horizon,
                    transaction_cost_bps=transaction_cost_bps,
                    slippage_bps=slippage_bps,
                )
                if value is not None:
                    return value
    except Exception as exc:
        logger.debug("Forward return fetch failed for %s: %s", symbol, exc)

    # Fallback: try AKShare
    try:
        from tradingagents.dataflows.akshare_stock import get_stock
        td = datetime.strptime(signal_date, "%Y-%m-%d")
        end_dt = td + timedelta(days=horizon * 2 + 10)
        raw = get_stock(symbol, signal_date, end_dt.strftime("%Y-%m-%d"))
        if raw and not raw.startswith("Error"):
            import csv
            import io
            reader = csv.DictReader(io.StringIO(raw))
            rows = list(reader)
            return calculate_net_forward_return(
                rows,
                signal_date=signal_date,
                horizon=horizon,
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
            )
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
    transaction_cost_bps: float = 10.0,
    slippage_bps: float = 5.0,
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

        reviewer = None
        if mode in {"fused", "compare"}:
            reviewer = build_candidate_reviewer(config, style=style, trade_date=trade_date)
            if reviewer is None:
                await client.disconnect()
                raise RuntimeError(
                    "fused backtest requested but the configured LLM reviewer could not be built"
                )

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
            symbol = candidate["symbol"]

            # Get forward return
            forward_return = await get_forward_return(
                client, symbol, trade_date, horizon,
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
            )
            if forward_return is None:
                logger.debug("No forward return for %s on %s", symbol, trade_date)
                continue

            variants = ("quant_only", "fused") if mode == "compare" else (mode,)
            for variant in variants:
                fusion = await fuse_for_backtest(
                    candidate, style=style, mode=variant, reviewer=reviewer
                )
                signal = fusion["signal"]
                results.append({
                    "trade_date": trade_date,
                    "symbol": symbol,
                    "name": candidate.get("name", ""),
                    "variant": variant,
                    "signal": signal,
                    "final_score": fusion["final_score"],
                    "quant_score": fusion.get("quant_score"),
                    "llm_score": fusion.get("llm_score"),
                    "llm_confidence": fusion.get("llm_confidence"),
                    "llm_view": fusion.get("llm_view"),
                    "forward_return_pct": forward_return,
                    "direction_correct": is_signal_correct(signal, forward_return),
                    "horizon": horizon,
                    "fusion_mode": fusion.get("fusion_mode", "quant_only"),
                    "return_basis": "next_session_open_to_horizon_close_net_costs",
                })

    await client.disconnect()
    return results


def print_results(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    """Pretty-print backtest results."""
    metrics = (
        compute_comparison_metrics(results)
        if args.mode == "compare"
        else compute_metrics(results)
    )

    print("\n═══════════════════════════════════════════")
    print(" Signal Fusion Backtest Results")
    print("═══════════════════════════════════════════")
    print(f" Period: {args.start_date} → {args.end_date}")
    print(f" Horizon: {args.horizon} days forward")
    print(f" Universe: {args.universe}")
    print(f" Style: {args.style}")
    print(f" Mode: {args.mode}")
    print("───────────────────────────────────────────")
    if args.mode == "compare":
        print(f" Paired samples: {metrics['paired_count']}")
        print(f" Fused accuracy delta: {metrics['paired_accuracy_delta_pp']:+.2f} pp")
        print(f" Improved / degraded: {metrics['fused_improved']} / {metrics['fused_degraded']}")
        print("═══════════════════════════════════════════\n")
        output_path = Path(__file__).parent / f"backtest_results_{args.start_date}_{args.end_date}.json"
        output_path.write_text(json.dumps({"args": vars(args), "metrics": metrics, "signals": results}, ensure_ascii=False, indent=2))
        print(f" Results saved to: {output_path}")
        return
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
    parser.add_argument("--mode", default="quant_only", choices=["quant_only", "fused", "compare"])
    parser.add_argument("--limit", type=int, default=5, help="Top N candidates per day")
    parser.add_argument("--candidate-limit", type=int, default=80, help="MCP candidate pool size")
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
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
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
    ))

    if not results:
        print("\n⚠️  No results generated. Check MCP connection and date range.")
        sys.exit(1)

    print_results(results, args)


if __name__ == "__main__":
    main()
