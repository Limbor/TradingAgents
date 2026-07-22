"""Decision audit ledger and rule-backtest endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from tradingagents.core.decision_audit import DecisionAuditEngine, audit_summary
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.strategy_backtest import audit_backtest_result, backtest_request_id

router = APIRouter()


def _save_backtest_artifact(db: Any, item: dict[str, Any]) -> None:
    result = item.get("result") or {}
    validation = result.get("validation") or {}
    db.save_artifact(
        artifact_id=f"backtest:{item['id']}", run_id="", skill_id="strategy_backtest",
        artifact_type="backtest_report", title="候选量化规则回测",
        subtitle=f"{item['start_date']} → {item['end_date']}",
        subject_type="strategy", subject_id=item["strategy_type"], subject_name=item["strategy_type"],
        status=item["status"],
        summary=f"上线门禁：{'通过' if validation.get('production_gate_passed') else '未通过'}",
        content_markdown=(
            f"## 候选量化规则回测\n\n- 策略：{item['strategy_type']}\n- 配置：{item['config'].get('config_name', '-')}\n- 区间：{item['start_date']} → {item['end_date']}\n"
            f"- 总收益：{result.get('total_return', '-')}\n- 最大回撤：{result.get('max_drawdown', '-')}\n"
            f"- Sharpe：{result.get('sharpe', '-')}\n- Purged CV：{validation.get('purged_cv') or '-'}"
        ), payload=item, tags=["strategy_backtest", item["strategy_type"]],
    )


class AuditRunRequest(BaseModel):
    as_of_date: str | None = None
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20])
    limit: int = Field(default=20, ge=1, le=100)


class ExecutionRequest(BaseModel):
    decision_id: str = ""
    symbol: str
    action: str
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    executed_at: str | None = None
    realized_pnl: float | None = None
    source: str = "external_confirmed"

    @model_validator(mode="after")
    def validate_action(self):
        if self.action.lower() not in {"add", "buy", "reduce", "sell", "exit"}:
            raise ValueError("unsupported execution action")
        return self


class BacktestRequest(BaseModel):
    strategy_name: str = "ff_residual_csi800_main"
    config_name: str = "prod_ff_residual_csi800_tv15"
    start_date: str
    end_date: str
    initial_cash: float = Field(default=1_000_000, gt=0)
    universe_size: int = Field(default=300, ge=20, le=5000)

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        return self


@router.get("/decision-audit/summary")
async def get_audit_summary(request: Request, min_samples: int = 20):
    return audit_summary(request.app.state.db, min_samples=max(5, min(min_samples, 500)))


@router.get("/decision-audit/decisions")
async def list_decisions(request: Request, status: str | None = None,
                         symbol: str | None = None, limit: int = 100):
    return request.app.state.db.list_decision_records(status=status, symbol=symbol, limit=limit)


@router.get("/decision-audit/executions")
async def list_executions(request: Request, decision_id: str | None = None,
                          symbol: str | None = None, limit: int = 100):
    return request.app.state.db.list_trade_executions(
        decision_id=decision_id, symbol=symbol, limit=limit
    )


@router.post("/decision-audit/executions")
async def create_execution(request: Request, body: ExecutionRequest):
    db = request.app.state.db
    if body.decision_id:
        decision = db.get_decision_record(body.decision_id)
        if not decision:
            raise HTTPException(status_code=404, detail="Decision not found")
        if str(decision["symbol"]).upper() != body.symbol.upper():
            raise HTTPException(status_code=400, detail="Execution symbol does not match decision")
    item = db.save_trade_execution(
        execution_id=str(uuid.uuid4()), decision_id=body.decision_id,
        symbol=body.symbol, action=body.action, quantity=body.quantity,
        price=body.price,
        executed_at=body.executed_at or datetime.now(timezone.utc).isoformat(),
        realized_pnl=body.realized_pnl, source=body.source,
    )
    if body.decision_id:
        db.update_decision_record(body.decision_id, status="partially_realized")
    return item


@router.get("/decision-audit/outcomes")
async def list_outcomes(request: Request, decision_id: str | None = None, limit: int = 500):
    return request.app.state.db.list_decision_outcomes(decision_id=decision_id, limit=limit)


@router.post("/decision-audit/evaluate")
async def evaluate_decisions(request: Request, body: AuditRunRequest):
    horizons = tuple(sorted({max(1, min(int(value), 120)) for value in body.horizons}))
    return await DecisionAuditEngine(request.app.state.db, request.app.state.config).evaluate_due(
        as_of_date=body.as_of_date, horizons=horizons, limit=body.limit
    )


@router.post("/backtests")
async def create_backtest(request: Request, body: BacktestRequest):
    client = await get_mcp_client(request.app.state.config)
    if client is None:
        raise HTTPException(status_code=503, detail="StockManager MCP unavailable")
    catalog = await client.list_strategies_and_configs() or {}
    strategies = {str(item.get("name")): item for item in catalog.get("strategies", []) if isinstance(item, dict)}
    configs = {str(item.get("name")): item for item in catalog.get("configs", []) if isinstance(item, dict)}
    if body.strategy_name not in strategies or body.config_name not in configs:
        raise HTTPException(status_code=400, detail="Unknown MCP strategy or config; refresh /backtests/catalog")
    request_payload = {
        **body.model_dump(), "strategy_sha1": strategies[body.strategy_name].get("sha1"),
        "config_sha1": configs[body.config_name].get("sha1"),
    }
    backtest_id = backtest_request_id(request_payload)
    existing = request.app.state.db.get_backtest_run(backtest_id)
    if existing and existing["status"] in {"submitted", "running", "completed"}:
        return existing
    result = await client.run_backtest(
        strategy=body.strategy_name,
        config=body.config_name,
        start=body.start_date,
        end=body.end_date,
        initial_cash=body.initial_cash,
        universe_size=body.universe_size,
    )
    if not isinstance(result, dict) or result.get("status") == "error":
        message = str((result or {}).get("error") if isinstance(result, dict) else "invalid MCP response")
        request.app.state.db.save_backtest_run(
            backtest_id=backtest_id, strategy_type=body.strategy_name,
            start_date=body.start_date, end_date=body.end_date,
            config=request_payload, status="failed", error=message,
        )
        raise HTTPException(status_code=502, detail=message)
    job_id = str(result.get("job_id") or "")
    status = "submitted" if job_id else "completed"
    audit_config = {**request.app.state.config, **request_payload}
    stored_result = {} if job_id else await audit_backtest_result(client, result, audit_config)
    item = request.app.state.db.save_backtest_run(
        backtest_id=backtest_id, job_id=job_id, strategy_type=body.strategy_name,
        start_date=body.start_date, end_date=body.end_date,
        config=request_payload, status=status,
        result=stored_result,
    )
    if status == "completed":
        _save_backtest_artifact(request.app.state.db, item)
    return item


@router.get("/backtests/catalog")
async def get_backtest_catalog(request: Request):
    client = await get_mcp_client(request.app.state.config)
    if client is None:
        raise HTTPException(status_code=503, detail="StockManager MCP unavailable")
    payload = await client.list_strategies_and_configs()
    if not isinstance(payload, dict) or payload.get("status") == "error":
        raise HTTPException(status_code=502, detail="MCP strategy catalog unavailable")
    return payload


@router.get("/backtests")
async def list_backtests(request: Request, status: str | None = None, limit: int = 50):
    items = request.app.state.db.list_backtest_runs(status=status, limit=limit)
    refreshed = []
    for item in items:
        validation = (item.get("result") or {}).get("validation") or {}
        needs_reaudit = item["status"] == "completed" and validation.get("version") != 2
        if (
            item["status"] in {"submitted", "running"} and item.get("job_id")
        ) or needs_reaudit:
            refreshed.append(await get_backtest(request, item["id"], refresh=True))
        else:
            refreshed.append(item)
    return refreshed


@router.get("/backtests/{backtest_id}")
async def get_backtest(request: Request, backtest_id: str, refresh: bool = True):
    db = request.app.state.db
    item = db.get_backtest_run(backtest_id)
    if not item:
        raise HTTPException(status_code=404, detail="Backtest not found")
    validation = (item.get("result") or {}).get("validation") or {}
    needs_reaudit = item["status"] == "completed" and validation.get("version") != 2
    if refresh and needs_reaudit:
        client = await get_mcp_client(request.app.state.config)
        if client is not None:
            audit_config = {**request.app.state.config, **item["config"]}
            audited = await audit_backtest_result(client, item["result"], audit_config)
            item = db.save_backtest_run(
                backtest_id=item["id"], job_id=item["job_id"],
                strategy_type=item["strategy_type"], start_date=item["start_date"],
                end_date=item["end_date"], config=item["config"],
                status="completed", result=audited,
            )
            _save_backtest_artifact(db, item)
    elif refresh and item["status"] in {"submitted", "running"} and item.get("job_id"):
        client = await get_mcp_client(request.app.state.config)
        if client is not None:
            status_payload = await client.get_job_status(item["job_id"])
            remote_status = str((status_payload or {}).get("status") or "running").lower()
            if remote_status in {"completed", "success", "succeeded", "done"}:
                result = await client.get_job_result(item["job_id"])
                audit_config = {**request.app.state.config, **item["config"]}
                audited = await audit_backtest_result(client, result or {}, audit_config)
                item = db.save_backtest_run(
                    backtest_id=item["id"], job_id=item["job_id"],
                    strategy_type=item["strategy_type"], start_date=item["start_date"],
                    end_date=item["end_date"], config=item["config"],
                    status="completed", result=audited,
                )
                _save_backtest_artifact(db, item)
            elif remote_status in {"failed", "error", "cancelled"}:
                item = db.save_backtest_run(
                    backtest_id=item["id"], job_id=item["job_id"],
                    strategy_type=item["strategy_type"], start_date=item["start_date"],
                    end_date=item["end_date"], config=item["config"],
                    status="failed", error=str((status_payload or {}).get("error") or remote_status),
                )
            else:
                item = db.save_backtest_run(
                    backtest_id=item["id"], job_id=item["job_id"],
                    strategy_type=item["strategy_type"], start_date=item["start_date"],
                    end_date=item["end_date"], config=item["config"], status="running",
                )
    return item
