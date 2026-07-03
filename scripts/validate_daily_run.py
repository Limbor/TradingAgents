"""Single-day live validation for signal fusion pipeline.

Run the full daily pipeline for today (or a specified date), save results,
then schedule a follow-up check after N trading days to compute accuracy.

Phase A (signal generation):
    python scripts/validate_daily_run.py --phase generate --date 2026-06-28

Phase B (result verification, run N days later):
    python scripts/validate_daily_run.py --phase verify --date 2026-06-28 --horizon 5

Signal files are saved to ~/.tradingagents/validations/<date>.json
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.core.mcp_client import StockManagerMCPClient, config_from_app_config
from tradingagents.core.persistence import Database
from tradingagents.core.signal_fusion import fuse_candidate_signal
from tradingagents.core.llm_candidate_review import build_candidate_reviewer
from tradingagents.core.candidate_enrichment import enrich_candidates
from tradingagents.dataflows.mcp_adapter import normalize_quant_candidate, payload_rows, payload_warnings
from tradingagents.default_config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

VALIDATION_DIR = Path.home() / ".tradingagents" / "validations"


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


async def generate_signals(
    trade_date: str,
    universe: str = "000906.SH",
    style: str = "medium_term",
    limit: int = 5,
    use_llm: bool = True,
) -> Path:
    """Run the full pipeline for a single date and save signals to JSON."""
    config = dict(DEFAULT_CONFIG)
    mcp_config = config_from_app_config(config)
    client = StockManagerMCPClient(mcp_config)

    try:
        connected = await client.connect()
        if not connected:
            raise RuntimeError("Cannot connect to StockManager MCP")
    except Exception as exc:
        raise RuntimeError(f"MCP connection failed: {exc}")

    # Step 1: Get quant rankings
    logger.info("Fetching quant rankings for %s...", trade_date)
    payload = await client.rank_factor_candidates(
        universe_index=universe,
        trade_date=trade_date,
        style=style,
        limit=limit,
        candidate_limit=80,
        factor_profile=_factor_profile_for_style(style),
        filters=_default_filters(),
        sector_prefs=[],
        return_factor_snapshot=True,
    )

    rows = payload_rows(payload)
    actual_trade_date = trade_date

    # Fallback: Tushare index_weight is MONTHLY data (published at month-end).
    # Try month-end dates going back up to 6 months, plus a few nearby calendar days.
    if not rows:
        from datetime import date as _date_type
        try:
            current = datetime.strptime(trade_date, "%Y-%m-%d").date()
        except ValueError:
            current = datetime.now().date()

        fallback_payload = None
        # Strategy 1: try nearby calendar days (up to 10 days)
        nearby_dates = [(current - timedelta(days=offset)).isoformat() for offset in range(1, 11)]
        # Strategy 2: try month-end dates going back 6 months (Tushare index_weight is monthly)
        import calendar
        month_end_dates = []
        d = current
        for _ in range(6):
            d = d.replace(day=1) - timedelta(days=1)  # last day of previous month
            month_end_dates.append(d.isoformat())

        # Deduplicate while preserving order
        seen = set()
        candidate_dates = []
        for dt_str in nearby_dates + month_end_dates:
            if dt_str not in seen and dt_str < trade_date:
                seen.add(dt_str)
                candidate_dates.append(dt_str)

        for fallback_date in candidate_dates:
            logger.info(
                "No candidates for %s; trying %s...",
                trade_date, fallback_date,
            )
            fallback_payload = await client.rank_factor_candidates(
                universe_index=universe,
                trade_date=fallback_date,
                style=style,
                limit=limit,
                candidate_limit=80,
                factor_profile=_factor_profile_for_style(style),
                filters=_default_filters(),
                sector_prefs=[],
                return_factor_snapshot=True,
            )
            rows = payload_rows(fallback_payload)
            if rows:
                logger.info("Found %d candidates using fallback date %s", len(rows), fallback_date)
                actual_trade_date = fallback_date
                payload = fallback_payload
                break

        if not rows:
            status = (payload or {}).get("status")
            message = (payload or {}).get("message") or "Unknown error"
            await client.disconnect()
            raise RuntimeError(
                f"No candidates returned for {trade_date} or any fallback date.\n"
                f"  Tushare index_weight data is MONTHLY (published at month-end).\n"
                f"  Try using --date 2026-04-30 (or another month-end with available data).\n"
                f"  Last MCP response: status={status}, message={message}"
            )

    candidates = []
    for row in rows[:limit]:
        candidate = normalize_quant_candidate(row)
        fusion = fuse_candidate_signal(candidate, style)
        candidate.update(fusion)
        candidates.append(candidate)

    # Step 2: Enrich with real-time data (use actual trade date from MCP)
    logger.info("Enriching %d candidates with market context...", len(candidates))
    context_map = {}
    try:
        context_map = await enrich_candidates(candidates, actual_trade_date, config)
    except Exception as exc:
        logger.warning("Enrichment failed: %s", exc)

    # Step 3: LLM review (optional)
    if use_llm:
        reviewer = build_candidate_reviewer(config, style=style, trade_date=actual_trade_date)
        if reviewer:
            logger.info("Running LLM reviews...")
            for candidate in candidates:
                try:
                    symbol = candidate.get("symbol", "")
                    ctx = context_map.get(symbol)
                    review = await reviewer.review(candidate, context=ctx)
                    from tradingagents.core.llm_candidate_review import CandidateLLMReview
                    if isinstance(review, dict):
                        review = CandidateLLMReview.model_validate(review)
                    candidate["llm_review"] = review.model_dump()
                    candidate.update(
                        fuse_candidate_signal(candidate, style, review.as_fusion_payload())
                    )
                except Exception as exc:
                    logger.warning("LLM review failed for %s: %s", candidate.get("symbol"), exc)

    await client.disconnect()

    # Save signals (filename uses actual trade date for consistency)
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    output_path = VALIDATION_DIR / f"{actual_trade_date}.json"
    output = {
        "trade_date": actual_trade_date,
        "requested_date": trade_date,
        "is_fallback_date": actual_trade_date != trade_date,
        "universe": universe,
        "style": style,
        "generated_at": datetime.now().isoformat(),
        "use_llm": use_llm,
        "signals": candidates,
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    logger.info("Signals saved to: %s", output_path)
    return output_path


async def verify_signals(trade_date: str, horizon: int = 5) -> dict[str, Any]:
    """Load saved signals and compute accuracy against actual returns."""
    # Try exact date first, then search nearby dates (fallback from generate)
    signal_path = VALIDATION_DIR / f"{trade_date}.json"
    if not signal_path.exists():
        # Search for fallback files (up to 5 days before)
        try:
            current = datetime.strptime(trade_date, "%Y-%m-%d").date()
        except ValueError:
            current = datetime.now().date()
        for offset in range(1, 6):
            fallback_date = (current - timedelta(days=offset)).isoformat()
            candidate_path = VALIDATION_DIR / f"{fallback_date}.json"
            if candidate_path.exists():
                signal_path = candidate_path
                logger.info("Using fallback signal file: %s", signal_path)
                break

    if not signal_path.exists():
        raise FileNotFoundError(
            f"No signals found for {trade_date} (or nearby dates). "
            f"Run --phase generate --date {trade_date} first."
        )

    data = json.loads(signal_path.read_text())
    signals = data["signals"]
    # Use actual trade date from file for forward return calculation
    actual_signal_date = data.get("trade_date", trade_date)
    logger.info("Loaded signals from %s (actual signal date: %s)", signal_path, actual_signal_date)

    # Connect to MCP for forward returns
    config = dict(DEFAULT_CONFIG)
    mcp_config = config_from_app_config(config)
    client = StockManagerMCPClient(mcp_config)

    try:
        connected = await client.connect()
    except Exception:
        connected = False

    results = []
    for signal_data in signals:
        symbol = signal_data.get("symbol", "")
        signal = signal_data.get("signal", "WATCHLIST")
        final_score = signal_data.get("final_score", 0)
        stop_loss = signal_data.get("stop_loss")

        # Get forward return
        forward_return = None
        stop_triggered = False

        if connected:
            try:
                td = datetime.strptime(actual_signal_date, "%Y-%m-%d")
                start = td.strftime("%Y%m%d")
                end = (td + timedelta(days=horizon + 10)).strftime("%Y%m%d")

                price_data = await client.get_stock_daily(
                    ts_codes=[symbol],
                    start_date=start,
                    end_date=end,
                    adj_type="qfq",
                )
                if price_data and isinstance(price_data, dict):
                    rows = price_data.get("rows") or price_data.get("data", {}).get("rows") or []
                    if len(rows) > horizon:
                        close_start = float(rows[0].get("close", 0))
                        close_end = float(rows[min(horizon, len(rows) - 1)].get("close", 0))
                        if close_start > 0:
                            forward_return = round((close_end / close_start - 1) * 100, 2)

                        # Check if stop loss was triggered
                        if stop_loss and close_start > 0:
                            for row in rows[1:horizon + 1]:
                                low = float(row.get("low", close_start))
                                if low <= stop_loss:
                                    stop_triggered = True
                                    break
            except Exception as exc:
                logger.debug("Price fetch failed for %s: %s", symbol, exc)

        if forward_return is not None:
            from scripts.backtest_signal_fusion import is_signal_correct
            results.append({
                "symbol": symbol,
                "name": signal_data.get("name", ""),
                "signal": signal,
                "final_score": final_score,
                "fusion_mode": signal_data.get("fusion_mode", ""),
                "forward_return_pct": forward_return,
                "direction_correct": is_signal_correct(signal, forward_return),
                "stop_triggered": stop_triggered,
                "key_catalysts": signal_data.get("key_catalysts", []),
                "key_risks": signal_data.get("key_risks", []),
            })

    if connected:
        await client.disconnect()

    # Compute metrics
    if not results:
        return {"error": "No forward returns available. Try again after market data is published."}

    total = len(results)
    correct = sum(1 for r in results if r["direction_correct"])
    buy_results = [r for r in results if r["signal"] == "BUY"]
    buy_correct = sum(1 for r in buy_results if r["direction_correct"])
    buy_returns = [r["forward_return_pct"] for r in buy_results]
    stop_triggered_count = sum(1 for r in buy_results if r["stop_triggered"])

    # LLM incremental value
    fused_results = [r for r in results if r["fusion_mode"] == "quant_llm_fused"]
    quant_only_results = [r for r in results if r["fusion_mode"] == "quant_only"]

    verification = {
        "trade_date": actual_signal_date,
        "requested_date": trade_date,
        "horizon": horizon,
        "verified_at": datetime.now().isoformat(),
        "total_signals": total,
        "overall_hit_rate": round(correct / total * 100, 1),
        "buy_count": len(buy_results),
        "buy_hit_rate": round(buy_correct / len(buy_results) * 100, 1) if buy_results else None,
        "avg_buy_return": round(sum(buy_returns) / len(buy_returns), 2) if buy_returns else None,
        "stop_trigger_rate": round(stop_triggered_count / len(buy_results) * 100, 1) if buy_results else None,
        "fused_count": len(fused_results),
        "quant_only_count": len(quant_only_results),
        "detailed_results": results,
    }

    # Save verification (filename uses actual signal date)
    verify_path = VALIDATION_DIR / f"{actual_signal_date}_verify_h{horizon}.json"
    verify_path.write_text(json.dumps(verification, ensure_ascii=False, indent=2))

    # Print summary
    print("\n═══════════════════════════════════════════")
    print(" Daily Signal Verification Report")
    print("═══════════════════════════════════════════")
    print(f" Signal date: {actual_signal_date}")
    if actual_signal_date != trade_date:
        print(f" (requested: {trade_date}, used fallback)")
    print(f" Verification horizon: {horizon} trading days")
    print("───────────────────────────────────────────")
    print(f" Total verified: {total}")
    print(f" Overall accuracy: {verification['overall_hit_rate']}%")
    if buy_results:
        print(f" BUY signals: {len(buy_results)} (hit rate: {verification['buy_hit_rate']}%)")
        print(f" Avg BUY return: {verification['avg_buy_return']:+.2f}%")
        print(f" Stop-loss triggered: {stop_triggered_count}/{len(buy_results)} ({verification['stop_trigger_rate']}%)")
    print("───────────────────────────────────────────")
    print(" Individual results:")
    for r in results:
        icon = "✓" if r["direction_correct"] else "✗"
        print(f"   {icon} {r['symbol']} ({r['name']}) | {r['signal']} | return: {r['forward_return_pct']:+.2f}%")
    print("═══════════════════════════════════════════")
    print(f"\n Results saved to: {verify_path}")

    return verification


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-day pipeline validation")
    parser.add_argument("--phase", required=True, choices=["generate", "verify"])
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="Trade date (YYYY-MM-DD)")
    parser.add_argument("--horizon", type=int, default=5, help="Forward verification horizon (days)")
    parser.add_argument("--universe", default="000906.SH")
    parser.add_argument("--style", default="medium_term", choices=["short_term", "medium_term", "long_term"])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM review (quant-only)")
    args = parser.parse_args()

    if args.phase == "generate":
        try:
            path = asyncio.run(generate_signals(
                trade_date=args.date,
                universe=args.universe,
                style=args.style,
                limit=args.limit,
                use_llm=not args.no_llm,
            ))
            # Extract actual date from filename (may differ from requested date due to fallback)
            actual_date = path.stem  # e.g. "2026-06-26.json" → "2026-06-26"
            if actual_date != args.date:
                print(f"\n✓ Signals generated (requested {args.date}, used fallback {actual_date})")
            else:
                print(f"\n✓ Signals generated for {args.date}")
            print(f"  Saved to: {path}")
            print(f"\n  Run verification after {args.horizon} trading days:")
            print(f"    python scripts/validate_daily_run.py --phase verify --date {args.date} --horizon {args.horizon}")
        except Exception as exc:
            print(f"\n✗ Generation failed: {exc}")
            sys.exit(1)

    elif args.phase == "verify":
        try:
            asyncio.run(verify_signals(args.date, args.horizon))
        except FileNotFoundError as exc:
            print(f"\n✗ {exc}")
            sys.exit(1)
        except Exception as exc:
            print(f"\n✗ Verification failed: {exc}")
            sys.exit(1)


if __name__ == "__main__":
    main()
