"""Reflection listing, summary, and manual trigger endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class ReflectionItem(BaseModel):
    id: str
    run_id: str | None = None
    ticker: str
    trade_date: str
    original_decision: str
    actual_return: float | None = None
    was_correct: bool | None = None
    reflection_text: str | None = None
    created_at: str


class ReflectionSummary(BaseModel):
    total: int = 0
    correct: int = 0
    incorrect: int = 0
    accuracy: float = 0.0
    lookback_days: int = 30


class TriggerResponse(BaseModel):
    status: str
    message: str


class LessonDeactivateResponse(BaseModel):
    status: str
    lesson_id: str
    message: str


class ReflectionCaseItem(BaseModel):
    id: str
    source_type: str
    reflection_scope: str
    eligible_for_strategy_learning: bool
    status: str
    symbol: str
    name: str | None = None
    signal_date: str
    horizon_days: int
    due_date: str | None = None
    source_run_id: str = ""
    source_artifact_id: str = ""
    snapshot_payload: dict[str, Any] = Field(default_factory=dict)
    outcome_payload: dict[str, Any] = Field(default_factory=dict)
    post_signal_evidence_payload: dict[str, Any] = Field(default_factory=dict)
    attribution_payload: dict[str, Any] = Field(default_factory=dict)
    lesson_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class StrategyLessonItem(BaseModel):
    id: str
    lesson_type: str
    scope: str
    target: str = ""
    finding: str
    suggested_adjustment: str = ""
    evidence_count: int = 1
    confidence: str = "low"
    active: bool = True
    governance_status: str = "approved"
    expires_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ScorecardAggregate(BaseModel):
    count: int = 0
    directional_count: int = 0
    hit_rate: float | None = None
    avg_return: float | None = None
    avg_excess: float | None = None


class ScorecardBucket(ScorecardAggregate):
    bucket: str


class ScorecardRankIC(BaseModel):
    value: float | None = None
    n: int = 0


class AlphaSuggestion(BaseModel):
    """Advisory adaptive-alpha recommendation (read-only, never auto-applied)."""

    suggested_alpha: float
    static_alpha: float
    alpha_data: float | None = None
    data_weight: float = 0.0
    delta: float = 0.0
    quant_ic: float | None = None
    llm_ic: float | None = None
    n: int = 0
    applicable: bool = False
    reason: str = ""
    style: str | None = None


class PredictionScorecard(BaseModel):
    """Read-only prediction-quality scorecard.

    ``available=False`` (with ``reason``) is returned when too few evaluated
    samples exist; the remaining fields are only populated when available.
    """

    available: bool = False
    reason: str | None = None
    n_evaluated: int = 0
    min_samples: int = 5
    lookback_days: int = 90
    as_of: str = ""
    overall: ScorecardAggregate | None = None
    rank_ic: dict[str, ScorecardRankIC] = Field(default_factory=dict)
    fusion_comparison: dict[str, ScorecardBucket | None] = Field(default_factory=dict)
    buckets: dict[str, list[ScorecardBucket]] = Field(default_factory=dict)
    horizon_distribution: dict[str, int] = Field(default_factory=dict)
    alpha_suggestion: AlphaSuggestion | None = None


class CandidateActionRequest(BaseModel):
    action: str
    symbol: str
    name: str | None = None
    run_id: str = ""
    artifact_id: str = ""
    trade_date: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CandidateActionResponse(BaseModel):
    status: str
    case_id: str | None = None
    message: str


@router.get("/reflections", response_model=list[ReflectionItem])
async def list_reflections(
    request: Request,
    ticker: str | None = None,
    limit: int = 20,
):
    """List reflection records, optionally filtered by ticker."""
    db = request.app.state.db
    rows = db.list_reflections(ticker=ticker, limit=limit)
    return [ReflectionItem(**row) for row in rows]


@router.get("/reflections/summary", response_model=ReflectionSummary)
async def get_reflection_summary(request: Request, lookback_days: int = 30):
    """Get accuracy statistics for recent reflections."""
    db = request.app.state.db
    stats = db.get_reflection_summary(lookback_days=lookback_days)
    return ReflectionSummary(
        total=stats.get("total", 0),
        correct=stats.get("correct", 0),
        incorrect=stats.get("incorrect", 0),
        accuracy=stats.get("accuracy", 0.0),
        lookback_days=lookback_days,
    )


@router.get("/prediction-scorecard", response_model=PredictionScorecard)
async def get_prediction_scorecard(request: Request, lookback_days: int = 90):
    """Read-only prediction-quality scorecard over recent reflected cases.

    Pure measurement layer: aggregates realized outcomes of past signals
    (hit-rate, RankIC, bucketed stats, quant-only vs fused comparison) without
    touching any decision, fusion weight, or gate. Degrades to
    ``available=false`` when too few evaluated samples exist.
    """
    scorecard = request.app.state.db.get_prediction_scorecard(lookback_days=lookback_days)
    return PredictionScorecard(**scorecard)


@router.get("/reflection-cases", response_model=list[ReflectionCaseItem])
async def list_reflection_cases(
    request: Request,
    status: str | None = None,
    symbol: str | None = None,
    reflection_scope: str | None = None,
    eligible_only: bool = False,
    limit: int = 50,
):
    """List layered reflection cases."""
    rows = request.app.state.db.list_reflection_cases(
        status=status,
        symbol=symbol,
        reflection_scope=reflection_scope,
        eligible_only=eligible_only,
        limit=limit,
    )
    from tradingagents.core.trading_time import advance_trading_days

    items: list[ReflectionCaseItem] = []
    for row in rows:
        due = advance_trading_days(
            str(row.get("signal_date") or ""),
            int(row.get("horizon_days") or 5),
        )
        items.append(ReflectionCaseItem(**row, due_date=due))
    return items


@router.get("/strategy-lessons", response_model=list[StrategyLessonItem])
async def list_strategy_lessons(
    request: Request,
    active_only: bool = True,
    lesson_type: str | None = None,
    limit: int = 20,
):
    """List active strategy lessons used by future analysis."""
    rows = request.app.state.db.list_strategy_lessons(
        active_only=active_only,
        lesson_type=lesson_type,
        limit=limit,
    )
    return [StrategyLessonItem(**row) for row in rows]


@router.post("/strategy-lessons/{lesson_id}/deactivate", response_model=LessonDeactivateResponse)
async def deactivate_strategy_lesson(request: Request, lesson_id: str):
    """Manually retire a strategy lesson (sets active=0) so it stops being
    injected into future candidate reviews. Returns ``status="not_found"`` only
    when no lesson row matches the id."""
    updated = request.app.state.db.deactivate_strategy_lesson(lesson_id)
    if updated:
        return LessonDeactivateResponse(
            status="ok",
            lesson_id=lesson_id,
            message="Lesson deactivated; it will no longer be injected.",
        )
    return LessonDeactivateResponse(
        status="not_found",
        lesson_id=lesson_id,
        message="No active lesson matched the given id.",
    )


@router.post("/strategy-lessons/{lesson_id}/approve", response_model=LessonDeactivateResponse)
async def approve_strategy_lesson(request: Request, lesson_id: str):
    """Approve a reviewed candidate so it may influence future analyses."""
    updated = request.app.state.db.approve_strategy_lesson(lesson_id)
    if updated:
        return LessonDeactivateResponse(
            status="ok",
            lesson_id=lesson_id,
            message="Lesson approved and enabled for future analyses.",
        )
    return LessonDeactivateResponse(
        status="not_found",
        lesson_id=lesson_id,
        message="No candidate or statistically validated lesson matched the id.",
    )


@router.get("/strategy-lessons/{lesson_id}/cases", response_model=list[ReflectionCaseItem])
async def list_lesson_cases(request: Request, lesson_id: str):
    """Return the reflection cases that produced a strategy lesson.

    Reads the ``evidence_cases`` ids the miner persisted into the lesson
    payload and resolves each to its full reflection case, letting the UI drill
    from a lesson back to its supporting evidence. Returns an empty list when
    the lesson is unknown or predates evidence tracking. Cases that can no
    longer be resolved (e.g. purged) are skipped.
    """
    db = request.app.state.db
    lesson = db.get_strategy_lesson(lesson_id)
    if not lesson:
        return []
    payload = lesson.get("payload") or {}
    evidence = payload.get("evidence_cases") if isinstance(payload, dict) else None
    if not isinstance(evidence, list):
        return []

    from tradingagents.core.trading_time import advance_trading_days

    items: list[ReflectionCaseItem] = []
    seen: set[str] = set()
    for entry in evidence:
        cid = str((entry or {}).get("id") if isinstance(entry, dict) else "") or ""
        if not cid or cid in seen:
            continue
        seen.add(cid)
        row = db.get_reflection_case(cid)
        if not row:
            continue
        due = advance_trading_days(
            str(row.get("signal_date") or ""),
            int(row.get("horizon_days") or 5),
        )
        items.append(ReflectionCaseItem(**row, due_date=due))
    return items


@router.post("/candidate-actions", response_model=CandidateActionResponse)
async def save_candidate_action(request: Request, body: CandidateActionRequest):
    """Record user intent for a candidate without forcing it into strategy learning."""
    import uuid

    from tradingagents.core.trading_time import get_temporal_context

    action = body.action.strip().lower()
    if action in {"adopt", "plan", "executed"}:
        scope = "decision_grade"
        eligible = True
        source_type = "system_signal"
        message = "已加入强反思闭环，后续会用于策略经验沉淀。"
    elif action in {"watch", "observe"}:
        scope = "candidate_pool"
        eligible = False
        source_type = "system_signal"
        message = "已加入观察池，不会直接影响策略学习。"
    elif action in {"private", "private_review"}:
        scope = "user_private"
        eligible = False
        source_type = "user_trade"
        message = "已保存为私人复盘，不会进入系统策略学习。"
    elif action in {"ignore", "dismiss"}:
        return CandidateActionResponse(status="ignored", message="已忽略该候选，不创建反思样本。")
    else:
        return CandidateActionResponse(status="error", message=f"Unsupported action: {body.action}")

    temporal_context = get_temporal_context(request.app.state.config, market="cn_a")
    trade_date = body.trade_date or body.payload.get("trade_date") or temporal_context.market_asof_date
    case_id = str(uuid.uuid4())
    request.app.state.db.save_reflection_case(
        case_id=case_id,
        source_type=source_type,
        reflection_scope=scope,
        eligible_for_strategy_learning=eligible,
        symbol=body.symbol,
        name=body.name,
        signal_date=str(trade_date),
        horizon_days=5,
        source_run_id=body.run_id,
        source_artifact_id=body.artifact_id,
        snapshot_payload={
            "user_action": action,
            "candidate": body.payload,
            "final_decision": body.payload.get("final_decision") or body.payload.get("signal"),
            "quant_decision": body.payload.get("quant_decision"),
            "llm_review": body.payload.get("llm_review"),
            "temporal_context": temporal_context.to_dict(),
        },
        status="pending",
    )
    return CandidateActionResponse(status="saved", case_id=case_id, message=message)


@router.post("/reflections/trigger", response_model=TriggerResponse)
async def trigger_reflection(request: Request, background_tasks: BackgroundTasks):
    """Manually trigger a reflection batch run."""
    from tradingagents.agents.utils.memory import TradingMemoryLog
    from tradingagents.core.reflection import ReflectionEngine

    config: dict[str, Any] = request.app.state.config
    db = request.app.state.db

    async def _run_reflection():
        try:
            memory_log = TradingMemoryLog(config)
            engine = ReflectionEngine(db=db, config=config, memory_log=memory_log)
            result = await engine.run_reflection_batch()
            logger.info("Manual reflection trigger completed: %s", result)
        except Exception as exc:
            logger.exception("Manual reflection trigger failed: %s", exc)

    background_tasks.add_task(_run_reflection)
    return TriggerResponse(status="accepted", message="Reflection batch triggered in background.")


class MinePatternsResponse(BaseModel):
    status: str
    total_cases: int = 0
    buckets_evaluated: int = 0
    significant_buckets: int = 0
    lessons_created: int = 0
    lessons_updated: int = 0
    message: str = ""


@router.post("/reflections/mine-patterns", response_model=MinePatternsResponse)
async def mine_patterns(request: Request, background_tasks: BackgroundTasks):
    """Manually trigger cross-symbol pattern mining.

    Runs in the background so the HTTP request returns immediately — mining
    can take a while when LLM explanation is enabled or the case volume is
    large. Use ``GET /reflections/strategy-lessons`` to inspect results after
    the job completes.
    """
    from tradingagents.core.cross_symbol_pattern_miner import CrossSymbolPatternMiner

    config: dict[str, Any] = request.app.state.config
    db = request.app.state.db

    async def _run_mining():
        try:
            miner = CrossSymbolPatternMiner(db=db, config=config)
            result = await miner.mine(
                lookback_days=config.get("cross_symbol_miner_lookback_days", 30),
                min_samples=config.get("cross_symbol_miner_min_samples", 5),
                min_lift=config.get("cross_symbol_miner_min_lift", 0.15),
            )
            logger.info("Manual pattern mining completed: %s", result)
        except Exception as exc:
            logger.exception("Manual pattern mining failed: %s", exc)

    background_tasks.add_task(_run_mining)
    return MinePatternsResponse(
        status="accepted",
        message="Pattern mining scheduled in background. Check strategy-lessons for results.",
    )
