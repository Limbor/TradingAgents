"""Close-of-day plan monitoring: evaluate active plans and flag triggers.

Called by the daily scheduler job (16:45 Asia/Shanghai, after the reflection
job) and by the manual "advance trading day" endpoint. Triggered plans are
marked ``status='triggered'`` and a ``plan_alert`` artifact is saved so the
reminder surfaces in Library / Dashboard. Every checked plan is also stamped
with ``last_checked_at`` / ``last_checked_trade_date`` so the user can confirm
the daily job actually ran.
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
        now = datetime.now(timezone.utc).isoformat()
        try:
            result = await evaluate_plan(plan, config=config, data=data)
        except Exception as exc:
            logger.warning("plan evaluation failed for %s: %s", plan.get("id"), exc)
            # Record that the monitor attempted this plan even when the quote
            # fetch fails, so the user can see the daily job actually ran.
            try:
                db.update_plan(plan["id"], last_checked_at=now)
            except Exception:
                logger.warning("failed to stamp last_checked_at for %s", plan.get("id"))
            continue
        # Stamp every checked plan (triggered or not) with the trade date the
        # check was based on, so the user can confirm the daily job ran and see
        # how stale any plan's last evaluation is. A missing trade_date (quote
        # unavailable) leaves the previous good date intact.
        update_fields: dict[str, Any] = {"last_checked_at": now}
        trade_date = result.get("trade_date")
        if trade_date is not None:
            update_fields["last_checked_trade_date"] = trade_date
        if result.get("triggered"):
            update_fields.update(
                status="triggered",
                triggered_at=now,
                trigger_reason=str(result.get("reason", "")),
            )
        db.update_plan(plan["id"], **update_fields)
        if not result.get("triggered"):
            continue
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
