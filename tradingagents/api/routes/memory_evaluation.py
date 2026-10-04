"""Explicit, bounded paired replay on archived signal-time candidate facts."""
from __future__ import annotations

import asyncio
import math
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tradingagents.core.agent_runtime import AgentContext, use_context
from tradingagents.core.llm_candidate_review import build_candidate_reviewer
from tradingagents.core.llm_usage import summarize_usage
from tradingagents.core.memory_evaluation import evaluate_memory_pairs
from tradingagents.core.model_policy import resolve_model
from tradingagents.core.strategy_memory import load_strategy_lessons, select_strategy_lessons

router = APIRouter()
MIN_PAIRS = 20


class EvaluationRequest(BaseModel):
    limit: int = Field(default=20, ge=20, le=40)


def replay_samples(db, style: str) -> list[dict]:
    """No current enrichment, private accounts, condition proxies or outcome inputs."""
    samples, seen, pools = [], set(), {}
    for case in reversed(db.list_reflection_cases(limit=2000, status="reflected")):
        snapshot = case.get("snapshot_payload") or {}
        candidate = snapshot.get("candidate")
        day = case.get("signal_date") or ""
        outcome = (case.get("outcome_payload") or {}).get("excess_return")
        if (case.get("source_type") != "system_signal" or snapshot.get("decision_brief") or
                not isinstance(candidate, dict) or not isinstance(candidate.get("quant_score"), (float, int)) or
                not day or not case.get("created_at") or case["created_at"][:10] > day or
                snapshot.get("info_cutoff", day) != day or
                isinstance(outcome, bool) or not isinstance(outcome, (float, int)) or not math.isfinite(outcome)):
            continue
        symbol = str(candidate.get("symbol") or candidate.get("ts_code") or "")
        key = (day, symbol)
        if not symbol or key in seen:
            continue
        seen.add(key)  # Choose the first archived version, never the best outcome.
        candidate = {**candidate, "horizon_days": case.get("horizon_days", 5)}
        if day not in pools:
            pools[day] = load_strategy_lessons(db, day)
        if not select_strategy_lessons(pools[day], {**candidate, "style": style}, as_of_date=day):
            continue
        samples.append({"trade_date": day, "snapshot_as_of": day, "candidate": deepcopy(candidate),
                        "excess_return": outcome, "case_id": case["id"]})
    return sorted(samples, key=lambda row: (row["trade_date"], row["candidate"].get("symbol", "")))


def _jobs(request):
    if not hasattr(request.app.state, "memory_evaluation_jobs"):
        request.app.state.memory_evaluation_jobs = {}
    return request.app.state.memory_evaluation_jobs


@router.get("/strategy-memory/evaluation-preview")
async def preview(request: Request):
    style = request.app.state.config.get("investment_style", "medium_term")
    samples = replay_samples(request.app.state.db, style)
    jobs = _jobs(request)
    active = next((key for key, task in jobs.items() if not task.done()), None)
    return {"available_pairs": len(samples), "minimum_pairs": MIN_PAIRS, "default_pairs": MIN_PAIRS,
            "max_model_calls": MIN_PAIRS * 2, "can_run": len(samples) >= MIN_PAIRS and active is None,
            "active_job_id": active, "style": style,
            "message": "仅比较当时已批准的适用经验；不足 20 对不运行。运行会消耗当前统一模型额度。"}


@router.post("/strategy-memory/evaluations", status_code=202)
async def start(request: Request, body: EvaluationRequest):
    jobs = _jobs(request)
    if any(not task.done() for task in jobs.values()):
        raise HTTPException(409, "已有对照评测正在运行")
    db = request.app.state.db
    config = dict(request.app.state.config)
    style = config.get("investment_style", "medium_term")
    samples = replay_samples(db, style)[:body.limit]
    if len(samples) < MIN_PAIRS:
        raise HTTPException(422, "信号时点具备已批准适用经验的完整历史样本不足 20 对；尚未调用模型")
    try:
        model = resolve_model(config, require_config=True)
    except ValueError as exc:
        raise HTTPException(422, "请先配置统一模型") from exc
    if not model:
        raise HTTPException(422, "请先配置统一模型")
    job_id = str(uuid.uuid4())
    state = {"status": "running", "completed_pairs": 0, "total_pairs": len(samples),
             "max_model_calls": len(samples) * 2, "model": model,
             "started_at": datetime.now(timezone.utc).isoformat()}

    def persist():
        db.save_artifact(artifact_id=job_id, run_id=job_id, skill_id="memory_evaluation",
                         artifact_type="memory_evaluation_job", title="记忆对照评测任务",
                         payload=state, summary=f"{state['status']} · {state['completed_pairs']}/{len(samples)} 对")

    persist()

    async def run():
        context = AgentContext.root({**config, "agent_max_tokens_per_task": 200_000},
                                    root_id=job_id, db=db, timeout=1200)
        context.budget.max_model_calls = len(samples) * 2

        def progress(count):
            state["completed_pairs"] = count
            persist()

        try:
            with use_context(context):
                report = await asyncio.wait_for(evaluate_memory_pairs(samples, db,
                    lambda day, lessons: build_candidate_reviewer(config, style=style, trade_date=day, strategy_lessons=lessons),
                    style=style, model_label=f"{config.get('llm_provider')}/{model}", on_progress=progress), timeout=1200)
            report["usage_stats"] = summarize_usage(db.list_agent_runtime(job_id))
            report["source_case_ids"] = [sample["case_id"] for sample in samples]
            report_id = str(uuid.uuid4())
            db.save_artifact(artifact_id=report_id, run_id=job_id, skill_id="memory_evaluation",
                             artifact_type="memory_evaluation", title="历史经验对照评测", payload=report,
                             summary=f"{report['memory_pairs']} 对适用样本；不等于账户收益")
            state.update(status="completed", report_id=report_id)
        except asyncio.CancelledError:
            context.cancel_scope.set()
            state.update(status="interrupted", error="评测已中断，不会自动重跑收费调用")
            raise
        except Exception:
            context.cancel_scope.set()
            state.update(status="failed", error="评测未完成，请查看执行记录；已发生调用仍计入用量，不会自动重试")
        finally:
            state["usage_stats"] = summarize_usage(db.list_agent_runtime(job_id))
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
            persist()

    jobs.clear()  # Completed jobs remain in Library; no growing task registry.
    jobs[job_id] = asyncio.create_task(run())
    return {"id": job_id, **state}


@router.get("/strategy-memory/evaluations/{job_id}")
async def status(job_id: str, request: Request):
    artifact = request.app.state.db.get_artifact(job_id)
    if not artifact or artifact["artifact_type"] != "memory_evaluation_job":
        raise HTTPException(404, "评测任务不存在")
    payload = artifact["payload"]
    if payload.get("status") == "running" and job_id not in _jobs(request):
        payload = {**payload, "status": "interrupted", "error": "服务重启中断了评测，不会自动重跑"}
    return {"id": job_id, **payload}
