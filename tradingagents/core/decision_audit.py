"""Auditable decision -> execution -> outcome -> reflection lifecycle."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any

from tradingagents.core.decision_validation import validate_decision_history
from tradingagents.core.reflection import ReflectionEngine
from tradingagents.core.trading_time import advance_trading_days


def decision_id(source_type: str, decision_date: str, symbol: str, discriminator: str = "") -> str:
    raw = f"{source_type}|{decision_date}|{symbol.upper()}|{discriminator}"
    return "decision:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class DecisionAuditEngine:
    def __init__(self, db: Any, config: dict[str, Any]):
        self.db = db
        self.config = config
        self.reflection = ReflectionEngine(db=db, config=config)

    async def evaluate_due(self, *, as_of_date: str | None = None,
                           horizons: tuple[int, ...] = (1, 5, 10, 20),
                           limit: int = 200) -> dict[str, Any]:
        as_of = as_of_date or date.today().isoformat()
        evaluated = skipped = 0
        warnings: list[str] = []
        benchmark_cache: dict[tuple[str, str, int], dict[str, Any] | None] = {}
        if hasattr(self.db, "list_due_decision_records"):
            records = self.db.list_due_decision_records(limit=limit)
        else:  # Compatibility for lightweight test doubles.
            records = self.db.list_decision_records(
                status="open", limit=limit, oldest_first=True
            )
        for record in records:
            requested = sorted({1, 5, 10, 20} | set(horizons) | {int(record.get("horizon_days") or 5)})
            existing = {int(row["horizon_days"]) for row in self.db.list_decision_outcomes(
                decision_id=record["id"]
            )}
            attempted = 0
            record_errors: list[str] = []
            for horizon in requested:
                if horizon in existing or advance_trading_days(record["decision_date"], horizon) > as_of:
                    continue
                attempted += 1
                outcome = await self.reflection.fetch_outcome(
                    record["symbol"], record["decision_date"], horizon
                )
                if not outcome:
                    skipped += 1
                    message = (
                        f"{record['id']} horizon {horizon}: price outcome unavailable; "
                        "retry scheduled"
                    )
                    warnings.append(message)
                    record_errors.append(f"horizon {horizon}: price unavailable")
                    continue
                benchmark_symbol = str((record.get("payload") or {}).get("universe_index") or
                                       self.config.get("decision_audit_benchmark") or "000300.SH")
                benchmark_key = (benchmark_symbol, record["decision_date"], horizon)
                if benchmark_key not in benchmark_cache:
                    benchmark_cache[benchmark_key] = await self._fetch_benchmark(
                        benchmark_symbol, record["decision_date"], horizon
                    )
                benchmark = benchmark_cache[benchmark_key]
                if not benchmark:
                    warnings.append(
                        f"{record['id']} horizon {horizon}: benchmark {benchmark_symbol} unavailable"
                    )
                self.db.save_decision_outcome(
                    outcome_id=f"{record['id']}:{horizon}",
                    decision_id=record["id"],
                    horizon_days=horizon,
                    as_of_date=advance_trading_days(record["decision_date"], horizon),
                    actual_return=float(outcome["actual_return"]),
                    close_at_signal=outcome.get("close_at_signal"),
                    close_at_horizon=outcome.get("close_at_horizon"),
                    benchmark_return=float(benchmark["actual_return"]) if benchmark else None,
                    source=str(outcome.get("source") or ""),
                    payload={**outcome, "benchmark_symbol": benchmark_symbol,
                             "benchmark_source": (benchmark or {}).get("source")},
                )
                evaluated += 1
            if attempted and hasattr(self.db, "record_decision_audit_attempt"):
                self.db.record_decision_audit_attempt(
                    record["id"],
                    error="; ".join(record_errors) if record_errors else None,
                )
            final_horizon = int(record.get("horizon_days") or 5)
            outcomes = self.db.list_decision_outcomes(decision_id=record["id"])
            final = next((row for row in outcomes if int(row["horizon_days"]) == final_horizon), None)
            if final:
                case_id = record.get("reflection_case_id") or f"audit:{record['id']}"
                decision = str(record["decision"]).upper()
                learnable = decision in {"BUY", "SELL", "ADD", "REDUCE", "EXIT", "OVERWEIGHT", "UNDERWEIGHT"}
                existing_case = self.db.get_reflection_case(case_id)
                if not existing_case or existing_case.get("status") in {"pending", "outcome_ready"}:
                    self.db.save_reflection_case(
                        case_id=case_id,
                        source_type=record["source_type"],
                        reflection_scope="decision_grade" if learnable else "candidate_pool",
                        eligible_for_strategy_learning=learnable,
                        symbol=record["symbol"],
                        name=record.get("name"),
                        signal_date=record["decision_date"],
                        horizon_days=final_horizon,
                        source_run_id=record.get("source_run_id") or "",
                        source_artifact_id=record.get("source_artifact_id") or "",
                        snapshot_payload={**(record.get("payload") or {}),
                                          "final_decision": record["decision"],
                                          "reference_price": record.get("reference_price")},
                        outcome_payload={"actual_return": final["actual_return"],
                                         "close_at_signal": final.get("close_at_signal"),
                                         "close_at_horizon": final.get("close_at_horizon"),
                                         "source": final.get("source")},
                        status="outcome_ready",
                    )
                completed_horizons = {int(row["horizon_days"]) for row in outcomes}
                tracking_complete = all(horizon in completed_horizons for horizon in requested)
                self.db.update_decision_record(
                    record["id"], status="realized" if tracking_complete else "tracking",
                    reflection_case_id=case_id,
                )
        return {"evaluated_outcomes": evaluated, "skipped": skipped,
                "selected_records": len(records),
                "as_of_date": as_of, "warnings": warnings}

    async def _fetch_benchmark(self, symbol: str, signal_date: str,
                               horizon_days: int) -> dict[str, Any] | None:
        from tradingagents.core.mcp_client import get_mcp_client

        client = await get_mcp_client(self.config)
        if client is None:
            return None
        end = datetime.strptime(signal_date, "%Y-%m-%d") + timedelta(days=horizon_days * 2 + 10)
        payload = await client.get_index_daily(
            symbol, signal_date.replace("-", ""), end.strftime("%Y%m%d")
        )
        if not isinstance(payload, dict):
            return None
        rows = payload.get("data") or payload.get("rows") or []
        if not isinstance(rows, list):
            return None
        result = self.reflection._compute_return_from_rows(rows, signal_date, horizon_days)
        if result:
            result["source"] = str(payload.get("source") or "mcp_index")
        return result


def audit_summary(db: Any, *, min_samples: int = 20) -> dict[str, Any]:
    decisions = db.list_decision_records(limit=500)
    executions = db.list_trade_executions(limit=1000)
    outcomes = db.list_decision_outcomes(limit=5000)
    outcome_by_decision: dict[str, list[dict]] = {}
    for outcome in outcomes:
        outcome_by_decision.setdefault(outcome["decision_id"], []).append(outcome)
    realized_cases = []
    for decision in decisions:
        final = next((row for row in outcome_by_decision.get(decision["id"], [])
                      if int(row["horizon_days"]) == int(decision["horizon_days"])), None)
        if final:
            realized_cases.append({
                "snapshot_payload": {"final_decision": decision["decision"]},
                "outcome_payload": {"actual_return": final["actual_return"]},
            })
    validation = validate_decision_history(realized_cases, min_samples=min_samples)
    linked = sum(1 for row in executions if row.get("decision_id"))
    decision_map = {row["id"]: row for row in decisions}
    execution_returns: list[float] = []
    for execution in executions:
        decision = decision_map.get(execution.get("decision_id"))
        if not decision or float(execution.get("price") or 0) <= 0:
            continue
        final = next((row for row in outcome_by_decision.get(decision["id"], [])
                      if int(row["horizon_days"]) == int(decision["horizon_days"])), None)
        close = (final or {}).get("close_at_horizon")
        if not isinstance(close, (int, float)):
            continue
        value = (float(close) - float(execution["price"])) / float(execution["price"])
        if str(execution.get("action") or "").lower() in {"sell", "reduce", "exit"}:
            value = -value
        execution_returns.append(value)
    execution_wins = sum(1 for value in execution_returns if value > 0)
    return {
        "decision_count": len(decisions),
        "open_count": sum(1 for row in decisions if row["status"] == "open"),
        "realized_count": len(realized_cases),
        "execution_count": len(executions),
        "linked_execution_count": linked,
        "execution_link_rate": round(linked / len(executions), 4) if executions else 0.0,
        "execution_validation": {
            "sample_count": len(execution_returns),
            "win_rate": round(execution_wins / len(execution_returns), 4) if execution_returns else 0.0,
            "average_directional_return": round(sum(execution_returns) / len(execution_returns), 6) if execution_returns else 0.0,
            "statistically_usable": len(execution_returns) >= min_samples,
        },
        "validation": validation,
    }
