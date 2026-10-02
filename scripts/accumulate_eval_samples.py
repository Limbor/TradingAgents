"""Accumulate unbiased historical evaluation samples (RankIC / hit-rate / excess).

This offline harness backfills *evaluation* reflection cases so we can answer
"is the strategy accurate, and which way to tune it" from realized history,
reusing the existing outcome-fetch and ``build_scorecard`` machinery.

Two tracks (``--track``):

  daily   Walk-forward daily-selection replay. For each historical trade_date,
          rank the universe (real historical ranking), fuse each Top-N
          candidate, and enroll it as an eval case. Mirrors
          ``backtest_signal_fusion.py`` but persists to ``reflection_cases``.

  single  Random cross-section. Randomly sample ``--sample-k`` symbols from the
          point-in-time index universe each period so samples span the FULL
          quant_score range — this removes the top-N range bias that makes IC
          computed only on daily Top-N optimistic. A small ``--llm-sample``
          subset additionally runs the real ``StockAnalysisSkill`` (with
          ``analysis_date`` in the past, so its temporal context enforces an
          info-cutoff with no lookahead) to yield LLM RankIC samples.

SAFETY INVARIANT (isolation): every enrolled case is tagged
``source_type=BACKTEST_EVAL_SOURCE_TYPE`` and written ONLY to
``reflection_cases`` — NEVER to ``decision_records`` (unlike the daily pipeline,
this script does not call ``update_decision_record``). The production audit gate
reads ``decision_records`` only, so it stays clean; the live scorecard and
reflection UI filter eval samples out via ``exclude_source_types``. Eval cases
are enrolled as non-eligible + written directly at ``status="reflected"`` (with
outcome backfilled), so the reflection batch never picks them up for lesson
mining either. When adaptive alpha is explicitly enabled, production may read
only the aggregate evaluation scorecard; it still never consumes individual
eval cases as lessons or decisions. With no eval rows present, the override
fails closed and static weights remain unchanged.

LESSON MATERIAL (``--extract-lessons``, on by default): for the real-LLM subset
only, each reflected case is additionally run through
``ReflectionEngine.generate_attribution`` (writing back ``attribution_payload``)
and ``maybe_create_strategy_lesson``. Because eval cases stay non-eligible, the
directional (``ex_ante_miss``) lesson path is gated OFF — only the neutral
opportunity-cost / risk-avoidance lessons surface. Every lesson lands as
``governance_status="candidate"`` + ``active=False`` (the engine default), so it
becomes reflection *material* for human review and NEVER auto-feeds production
prediction (``_load_strategy_lessons`` reads ``active_only=True``). This keeps
the isolation invariant intact while giving the reflection engine concrete
success/failure lessons distilled from the LLM samples.

Usage:
    # quant-only, both tracks, reproducible
    python scripts/accumulate_eval_samples.py \
        --track both --start 2024-01-02 --end 2024-03-29 \
        --universe 000906.SH --style medium_term --horizon 5 \
        --sample-k 20 --seed 42

    # add a small real-LLM subset for LLM RankIC
    python scripts/accumulate_eval_samples.py \
        --track single --start 2024-01-02 --end 2024-03-29 \
        --sample-k 20 --llm-sample 5 --seed 42
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Add project root to path so the script runs standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.core.mcp_client import StockManagerMCPClient, config_from_app_config
from tradingagents.core.reflection import ReflectionEngine
from tradingagents.core.reflection_enroll import (
    BACKTEST_EVAL_SOURCE_TYPE,
    enroll_reflection_case,
)
from tradingagents.core.signal_fusion import LLMAssessment, fuse_candidate_signal
from tradingagents.dataflows.mcp_adapter import normalize_quant_candidate, payload_rows
from tradingagents.default_config import DEFAULT_CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_BENCHMARK = "000300.SH"


def _factor_profile_for_style(style: str) -> str:
    """Mirror ``backtest_signal_fusion._factor_profile_for_style``."""
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


def trading_dates(start_date: str, end_date: str) -> list[str]:
    """Weekday dates in [start, end] (MCP skips non-trading days internally)."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates: list[str] = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # skip Sat/Sun
            dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


def eval_case_id(signal_date: str, symbol: str) -> str:
    """Deterministic case id, matching ``enroll_reflection_case`` construction.

    ``enroll_reflection_case`` builds ``f"{prefix}:{signal_date}:{SYMBOL}"`` with
    ``prefix`` defaulting to ``source_type``. Re-run on the same (date, symbol)
    therefore replaces rather than duplicates.
    """
    return f"{BACKTEST_EVAL_SOURCE_TYPE}:{signal_date or 'undated'}:{(symbol or 'unknown').upper()}"


def build_eval_snapshot(
    candidate: dict[str, Any],
    fusion: dict[str, Any],
    *,
    style: str,
    factor_profile: str,
) -> dict[str, Any]:
    """Snapshot the signal-time evidence needed by ``build_scorecard``.

    ``extract_features`` reads ``quant_score`` / ``llm_confidence`` /
    ``fusion_mode`` / ``investment_style`` / ``final_decision`` from the snapshot
    top level (with a ``candidate`` subdict fallback), so those live at the top.
    """
    snapshot: dict[str, Any] = {
        "symbol": candidate.get("symbol"),
        "name": candidate.get("name"),
        "industry": candidate.get("industry"),
        "quant_score": fusion.get("quant_score", candidate.get("quant_score")),
        "quant_decision": candidate.get("quant_decision"),
        "factor_scores": candidate.get("factor_scores"),
        "final_decision": fusion.get("final_decision"),
        "signal": fusion.get("signal"),
        "fusion_mode": fusion.get("fusion_mode"),
        "llm_score": fusion.get("llm_score"),
        "llm_confidence": fusion.get("llm_confidence"),
        "investment_style": style,
        "factor_profile": factor_profile,
    }
    snapshot = {key: value for key, value in snapshot.items() if value is not None}
    snapshot["candidate"] = {
        key: value for key, value in candidate.items() if key != "raw_payload"
    }
    return snapshot


def enroll_eval_case(
    db: Any,
    *,
    candidate: dict[str, Any],
    fusion: dict[str, Any],
    signal_date: str,
    style: str,
    factor_profile: str,
    horizon: int,
    run_id: str,
    dry_run: bool,
) -> str | None:
    """Enroll one evaluation case into ``reflection_cases`` (never decision_records).

    Returns the deterministic case id, or ``None`` in dry-run mode.
    """
    symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "")
    if not symbol:
        return None
    if dry_run:
        logger.info("[dry-run] would enroll eval case %s", eval_case_id(signal_date, symbol))
        return None
    enroll_reflection_case(
        db,
        source_type=BACKTEST_EVAL_SOURCE_TYPE,
        symbol=symbol,
        name=str(candidate.get("name") or symbol),
        signal_date=signal_date,
        rating_or_decision=fusion.get("final_decision") or fusion.get("signal"),
        source_run_id=run_id,
        snapshot_payload=build_eval_snapshot(
            candidate, fusion, style=style, factor_profile=factor_profile
        ),
        horizon_days=horizon,
        # Eval cases carry no strategy-learning weight: force non-eligible +
        # exploratory scope regardless of the signal so the lesson miner never
        # trains on backtest data.
        decision_grade_values=(),
        candidate_pool_values=(),
        default_scope="exploratory",
    )
    return eval_case_id(signal_date, symbol)


async def fetch_eval_outcome(
    engine: ReflectionEngine,
    symbol: str,
    signal_date: str,
    horizon: int,
    benchmark_symbol: str,
) -> dict[str, Any] | None:
    """Realized N-day outcome using the exact reflection 口径 (fractions).

    Reuses ``ReflectionEngine.fetch_outcome`` (MCP → AKShare → yfinance) and
    ``fetch_benchmark_return`` so ``actual_return`` / ``excess_return`` match the
    live scorecard's units. Module-level so tests can monkeypatch it.
    """
    outcome = await engine.fetch_outcome(symbol, signal_date, horizon)
    if not outcome or not isinstance(outcome.get("actual_return"), (int, float)):
        return None
    benchmark = await engine.fetch_benchmark_return(signal_date, horizon, benchmark_symbol)
    if benchmark is not None and isinstance(benchmark.get("actual_return"), (int, float)):
        outcome["benchmark_symbol"] = benchmark.get("benchmark_symbol")
        outcome["benchmark_return"] = benchmark.get("actual_return")
        outcome["excess_return"] = round(
            float(outcome["actual_return"]) - float(benchmark["actual_return"]), 4
        )
    return outcome


async def backfill_outcome(
    engine: ReflectionEngine,
    db: Any,
    *,
    case_id: str | None,
    symbol: str,
    signal_date: str,
    horizon: int,
    benchmark_symbol: str,
    dry_run: bool,
) -> bool:
    """Fetch realized outcome and mark the eval case ``reflected``.

    Historical horizons have already elapsed, so the outcome is immediately
    available. Returns True when an outcome was persisted.
    """
    outcome = await fetch_eval_outcome(engine, symbol, signal_date, horizon, benchmark_symbol)
    if outcome is None:
        logger.debug("No outcome for %s @ %s (+%dd)", symbol, signal_date, horizon)
        return False
    if dry_run or not case_id:
        return True
    db.update_reflection_case(case_id, status="reflected", outcome_payload=outcome)
    return True


async def extract_case_lesson(
    engine: ReflectionEngine,
    db: Any,
    *,
    case_id: str | None,
    dry_run: bool,
) -> dict[str, Any] | None:
    """Distill a *candidate* strategy lesson from one reflected LLM eval case.

    Invoked only for the real-LLM subset. Loads the just-reflected case (with its
    backfilled outcome), runs ``generate_attribution`` to write back an
    ``attribution_payload``, then ``maybe_create_strategy_lesson``. Directional
    (``ex_ante_miss``) lessons stay blocked by the
    ``eligible_for_strategy_learning`` gate (eval cases are non-eligible), so only
    neutral opportunity-cost / risk-avoidance lessons surface. Lessons are saved
    as ``governance_status="candidate"`` + ``active=False`` (engine default) —
    reflection *material* for human review, never auto-fed to production.

    Returns the created lesson dict, or ``None`` when nothing was distilled.
    """
    if dry_run or not case_id or engine is None:
        return None
    case = db.get_reflection_case(case_id)
    if not case:
        return None
    outcome = case.get("outcome_payload") or {}
    try:
        attribution = await engine.generate_attribution(case, outcome, {})
    except Exception as exc:  # best-effort; a failed attribution just skips the lesson
        logger.warning("Attribution failed for %s: %s", case_id, exc)
        return None
    db.update_reflection_case(case_id, attribution_payload=attribution)
    lesson = engine.maybe_create_strategy_lesson(case, attribution)
    return lesson or None


# Rating is converted only into a directional score.  It must never be used as
# epistemic confidence: a confident Sell is still a low directional score.
RATING_DIRECTION_SCORE = {
    "buy": 80.0,
    "overweight": 65.0,
    "hold": 50.0,
    "underweight": 35.0,
    "sell": 20.0,
}

def _confidence_from_conclusion(conclusion: dict[str, Any]) -> float | None:
    """Return only model-emitted epistemic confidence, never a direction proxy."""
    return _as_float(conclusion.get("confidence"))


async def analyze_with_llm(
    base_config: dict[str, Any],
    ticker: str,
    analysis_date: str,
) -> dict[str, Any] | None:
    """Run the real StockAnalysisSkill for a past date; return rating/confidence.

    Runs against a THROWAWAY temp directory + throwaway SQLite db so every skill
    side effect (report file, report row, analysis artifact, its own
    ``stock_analysis`` reflection case) lands in a scratch location that is
    deleted on return — the caller enrolls a ``backtest_eval`` case instead,
    preserving isolation regardless of whether the eval run targets the prod db
    or a temp db. Module-level so tests can monkeypatch it with a fake analyzer.
    """
    import shutil
    import tempfile

    from tradingagents.core.persistence import Database
    from tradingagents.skills.stock_analysis.skill import (
        StockAnalysisInput,
        StockAnalysisSkill,
    )

    scratch = tempfile.mkdtemp(prefix="eval_llm_")
    cfg = dict(base_config)
    # Redirect all persistence into the scratch dir/db (the graph does
    # os.makedirs(results_dir), so it must be a real path — not ""). The prod db
    # and results tree are never touched.
    cfg["results_dir"] = scratch
    cfg["db"] = Database(Path(scratch) / "throwaway.db")
    skill = StockAnalysisSkill()
    params = StockAnalysisInput(ticker=ticker, analysis_date=analysis_date)
    conclusion: dict[str, Any] | None = None
    try:
        async for event in skill.execute(params, cfg):
            if event.event_type == "skill_complete":
                data = event.data or {}
                sc = data.get("structured_conclusion")
                if isinstance(sc, dict):
                    conclusion = sc
    except Exception as exc:  # best-effort; a failed LLM run just skips that sample
        # The MCP streamable-http client can raise a noisy anyio cancel-scope error
        # while tearing down its transport AFTER skill_complete was already
        # emitted. Keep the conclusion we captured; only bail if we have none.
        if conclusion is None:
            logger.warning("LLM analysis failed for %s @ %s: %s", ticker, analysis_date, exc)
            return None
        logger.debug("Ignoring post-conclusion teardown error for %s: %s", ticker, exc)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if not conclusion:
        return None
    return {
        "rating": conclusion.get("rating"),
        "confidence": _confidence_from_conclusion(conclusion),
        "llm_score": RATING_DIRECTION_SCORE.get(
            str(conclusion.get("rating") or "").strip().lower()
        ),
    }


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _constituent_symbols(payload: dict[str, Any] | None) -> list[str]:
    """Extract ts_code symbols from a get_index_constituents payload.

    StockManager returns constituents as a flat ``constituents`` list of ts_code
    strings; older/other shapes expose dict rows. Handle both.
    """
    if not payload:
        return []
    symbols: list[str] = []
    raw = payload.get("constituents")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item:
                symbols.append(item.upper())
            elif isinstance(item, dict):
                code = item.get("ts_code") or item.get("con_code") or item.get("symbol") or item.get("code")
                if code:
                    symbols.append(str(code).upper())
    if symbols:
        return symbols
    for row in payload_rows(payload):
        code = row.get("ts_code") or row.get("con_code") or row.get("symbol") or row.get("code")
        if code:
            symbols.append(str(code).upper())
    return symbols


async def run_daily_track(
    client: StockManagerMCPClient,
    db: Any,
    engine: ReflectionEngine,
    args: argparse.Namespace,
) -> dict[str, int]:
    """Track one: walk-forward daily selection, enroll Top-N eval cases."""
    factor_profile = _factor_profile_for_style(args.style)
    enrolled = 0
    reflected = 0
    for i, trade_date in enumerate(trading_dates(args.start, args.end)):
        logger.info("[daily] %s (%d)", trade_date, i + 1)
        try:
            payload = await client.rank_factor_candidates(
                universe_index=args.universe,
                trade_date=trade_date,
                style=args.style,
                limit=args.limit,
                candidate_limit=args.candidate_limit,
                factor_profile=factor_profile,
                filters=_default_filters(),
                sector_prefs=[],
                return_factor_snapshot=True,
            )
        except Exception as exc:
            logger.warning("[daily] rank_factor_candidates failed %s: %s", trade_date, exc)
            continue
        rows = payload_rows(payload)
        for row in rows[: args.limit]:
            candidate = normalize_quant_candidate(row)
            fusion = fuse_candidate_signal(candidate, args.style)
            candidate.update(fusion)
            case_id = enroll_eval_case(
                db,
                candidate=candidate,
                fusion=fusion,
                signal_date=trade_date,
                style=args.style,
                factor_profile=factor_profile,
                horizon=args.horizon,
                run_id=f"eval_daily_{args.seed}",
                dry_run=args.dry_run,
            )
            enrolled += 1
            if await backfill_outcome(
                engine, db,
                case_id=case_id,
                symbol=candidate["symbol"],
                signal_date=trade_date,
                horizon=args.horizon,
                benchmark_symbol=args.benchmark,
                dry_run=args.dry_run,
            ):
                reflected += 1
    return {"enrolled": enrolled, "reflected": reflected}


async def run_single_track(
    client: StockManagerMCPClient,
    db: Any,
    engine: ReflectionEngine,
    args: argparse.Namespace,
    rng: random.Random,
    base_config: dict[str, Any],
) -> dict[str, int]:
    """Track two: random cross-section over the PIT universe (+ small LLM subset)."""
    factor_profile = _factor_profile_for_style(args.style)
    enrolled = 0
    reflected = 0
    llm_enrolled = 0
    lessons = 0
    extract_lessons = getattr(args, "extract_lessons", False)
    for i, trade_date in enumerate(trading_dates(args.start, args.end)):
        logger.info("[single] %s (%d)", trade_date, i + 1)
        # PIT universe (survivorship-safe): constituents as of the trade date.
        try:
            constituents = _constituent_symbols(
                await client.get_index_constituents(args.universe, trade_date)
            )
        except Exception as exc:
            logger.warning("[single] get_index_constituents failed %s: %s", trade_date, exc)
            continue
        if not constituents:
            continue
        # Real quant scores for the whole pool via rank_factor_candidates, then
        # sample randomly ACROSS the full ranking (not just the top) so the
        # accumulated IC is not biased to high-score names.
        try:
            payload = await client.rank_factor_candidates(
                universe_index=args.universe,
                trade_date=trade_date,
                style=args.style,
                limit=len(constituents),
                candidate_limit=max(args.candidate_limit, len(constituents)),
                factor_profile=factor_profile,
                filters=_default_filters(),
                sector_prefs=[],
                return_factor_snapshot=True,
            )
        except Exception as exc:
            logger.warning("[single] rank_factor_candidates failed %s: %s", trade_date, exc)
            continue
        scored = {
            (c := normalize_quant_candidate(row))["symbol"]: c
            for row in payload_rows(payload)
        }
        eligible = [s for s in constituents if s in scored]
        if not eligible:
            continue
        sample = rng.sample(eligible, min(args.sample_k, len(eligible)))
        llm_targets = set(sample[: max(0, args.llm_sample)])
        for symbol in sample:
            candidate = scored[symbol]
            llm_assessment = None
            if symbol in llm_targets:
                verdict = await analyze_with_llm(base_config, symbol, trade_date)
                rating = str((verdict or {}).get("rating") or "").strip().lower()
                direction_score = (verdict or {}).get("llm_score")
                if direction_score is None:
                    direction_score = RATING_DIRECTION_SCORE.get(rating)
                if verdict and direction_score is not None:
                    view = {
                        "buy": "strong_positive",
                        "overweight": "positive",
                        "hold": "neutral",
                        "underweight": "negative",
                        "sell": "strong_negative",
                    }.get(rating, "neutral")
                    llm_assessment = LLMAssessment(
                        llm_score=direction_score,
                        llm_confidence=verdict.get("confidence"),
                        llm_view=view,
                    )
            fuse_mode = args.mode == "fused" and llm_assessment is not None
            fusion = fuse_candidate_signal(
                candidate, args.style, llm_assessment if fuse_mode else None
            )
            candidate.update(fusion)
            # Even in quant_only mode, record the LLM confidence so it feeds the
            # LLM RankIC (extract_features reads llm_confidence independently).
            if llm_assessment is not None and not fuse_mode:
                fusion = dict(fusion)
                fusion["llm_score"] = llm_assessment.llm_score
                fusion["llm_confidence"] = llm_assessment.llm_confidence
            case_id = enroll_eval_case(
                db,
                candidate=candidate,
                fusion=fusion,
                signal_date=trade_date,
                style=args.style,
                factor_profile=factor_profile,
                horizon=args.horizon,
                run_id=f"eval_single_{args.seed}",
                dry_run=args.dry_run,
            )
            enrolled += 1
            if llm_assessment is not None:
                llm_enrolled += 1
            if await backfill_outcome(
                engine, db,
                case_id=case_id,
                symbol=symbol,
                signal_date=trade_date,
                horizon=args.horizon,
                benchmark_symbol=args.benchmark,
                dry_run=args.dry_run,
            ):
                reflected += 1
                # Distill a candidate lesson from the real-LLM subset only.
                if (
                    extract_lessons
                    and llm_assessment is not None
                    and await extract_case_lesson(
                        engine, db, case_id=case_id, dry_run=args.dry_run
                    )
                ):
                    lessons += 1
    return {
        "enrolled": enrolled,
        "reflected": reflected,
        "llm_enrolled": llm_enrolled,
        "lessons": lessons,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """Wire up MCP client + DB + reflection engine and run the requested tracks."""
    from tradingagents.core.persistence import Database

    config = dict(DEFAULT_CONFIG)
    mcp_config = config_from_app_config(config)
    client = StockManagerMCPClient(mcp_config)
    try:
        connected = await client.connect()
    except Exception as exc:
        logger.error("MCP connection failed: %s", exc)
        return {"error": "mcp_connect_failed"}
    if not connected:
        logger.error("Cannot connect to StockManager MCP; accumulation requires MCP.")
        return {"error": "mcp_unavailable"}

    db = Database(Path(args.db_path)) if args.db_path else Database()
    config["db"] = db
    engine = ReflectionEngine(db, config)
    rng = random.Random(args.seed)

    summary: dict[str, Any] = {}
    try:
        if args.track in ("daily", "both"):
            summary["daily"] = await run_daily_track(client, db, engine, args)
        if args.track in ("single", "both"):
            summary["single"] = await run_single_track(
                client, db, engine, args, rng, config
            )
    finally:
        await client.disconnect()
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Accumulate historical evaluation samples")
    parser.add_argument("--track", choices=["daily", "single", "both"], default="both")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--universe", default="000906.SH", help="Universe index (default CSI800)")
    parser.add_argument(
        "--style", default="medium_term", choices=["short_term", "medium_term", "long_term"]
    )
    parser.add_argument("--horizon", type=int, default=5, help="Forward-return horizon (trading days)")
    parser.add_argument("--mode", default="quant_only", choices=["quant_only", "fused"])
    parser.add_argument("--sample-k", type=int, default=20, help="Single-track symbols sampled per period")
    parser.add_argument("--llm-sample", type=int, default=0, help="Real-LLM subset size per period (0 = pure quant)")
    parser.add_argument(
        "--extract-lessons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Distill candidate strategy lessons from the real-LLM subset (single track)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Daily-track Top-N per date")
    parser.add_argument("--candidate-limit", type=int, default=200, help="MCP candidate pool size")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (reproducibility)")
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK, help="Excess-return benchmark index")
    parser.add_argument("--db-path", default="", help="Override SQLite DB path (default ~/.tradingagents/app.db)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to the DB; log only")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    summary = asyncio.run(run(args))
    if summary.get("error"):
        print(f"\n⚠️  Accumulation aborted: {summary['error']}")
        sys.exit(1)
    print("\n═══════════════════════════════════════════")
    print(" Evaluation Sample Accumulation")
    print("═══════════════════════════════════════════")
    print(f" Period: {args.start} → {args.end}  |  Universe: {args.universe}  |  Style: {args.style}")
    print(f" Track: {args.track}  |  Horizon: {args.horizon}d  |  Seed: {args.seed}"
          + ("  |  DRY-RUN" if args.dry_run else ""))
    for track, stats in summary.items():
        print(f" [{track}] {stats}")
    print("═══════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
