"""Close-of-day plan monitoring: evaluate active plans and flag triggers.

Called by the daily scheduler job (16:45 Asia/Shanghai, after the reflection
job) and by the manual "advance trading day" endpoint. Triggered plans are
marked ``status='triggered'`` and a ``plan_alert`` artifact is saved so the
reminder surfaces in Library / Dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from tradingagents.core.plan_evaluator import evaluate_plan

logger = logging.getLogger(__name__)


async def evaluate_active_plans(
    db: Any,
    config: dict[str, Any],
    *,
    data: Any = None,
) -> list[dict[str, Any]]:
    """Evaluate every active plan; update triggered ones; emit plan_alert artifacts.

    ``data`` is an optional injected :class:`PlanDataProvider` for testing;
    production callers leave it None so the live provider is used. Returns the
    list of triggered alerts so the caller (scheduler / endpoint) can surface
    them to the user.
    """
    from tradingagents.core.artifacts import save_skill_artifact

    active = db.list_plans(status="active", limit=200)
    alerts: list[dict[str, Any]] = []
    for plan in active:
        try:
            result = await evaluate_plan(plan, config=config, data=data)
        except Exception as exc:
            logger.warning("plan evaluation failed for %s: %s", plan.get("id"), exc)
            continue
        if not result.get("triggered"):
            continue
        now = datetime.now(timezone.utc).isoformat()
        db.update_plan(
            plan["id"],
            status="triggered",
            triggered_at=now,
            trigger_reason=str(result.get("reason", "")),
        )
        alert = {
            "plan_id": plan["id"],
            "symbol": plan.get("symbol"),
            "name": plan.get("name"),
            "reason": result.get("reason"),
            "details": result.get("details"),
            "price": result.get("price"),
            "trade_date": result.get("trade_date"),
            "triggered_at": now,
        }
        alerts.append(alert)
        try:
            save_skill_artifact(
                config,
                skill_id="plan_monitor",
                artifact_type="plan_alert",
                title=f"{plan.get('symbol')} 计划触发提醒",
                subtitle=str(result.get("reason", "")),
                subject_type="ticker",
                subject_id=str(plan.get("symbol") or ""),
                subject_name=str(plan.get("symbol") or ""),
                summary=str(result.get("reason", "")),
                content_markdown="\n".join(f"- {d}" for d in result.get("details", [])),
                payload=alert,
                tags=["plan_alert", str(plan.get("symbol") or "")],
            )
        except Exception as exc:
            logger.warning("failed to save plan_alert artifact: %s", exc)
    return alerts
