from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field, model_validator

from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.strategy_backtest import audit_backtest_result, backtest_request_id
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


class StrategyBacktestInput(BaseModel):
    strategy_name: str = "ff_residual_csi800_main"
    config_name: str = "prod_ff_residual_csi800_tv15"
    start_date: str
    end_date: str
    initial_cash: float = Field(default=1_000_000, gt=0)
    universe_size: int = Field(default=300, ge=20, le=5000)

    @model_validator(mode="after")
    def validate_supported(self):
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        return self


class StrategyBacktestOutput(BaseModel):
    backtest_id: str
    job_id: str = ""
    status: str


class StrategyBacktestSkill(BaseSkill):
    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="strategy_backtest", name="Strategy Backtest",
            description="Backtest a versioned StockManager quant rule used as evidence for DailyPipeline.",
            version="1.0.0", triggers=["策略回测", "回测选股", "回测 DailyPipeline", "backtest"],
            icon="flask-conical", category="research",
        )

    @property
    def input_schema(self):
        return StrategyBacktestInput

    @property
    def output_schema(self):
        return StrategyBacktestOutput

    async def execute(self, params: BaseModel, config: dict[str, Any]) -> AsyncIterator[SkillEvent]:
        values: StrategyBacktestInput = params
        db = config["db"]
        client = await get_mcp_client(config)
        if client is None:
            yield SkillEvent(event_type="skill_complete", data={"status": "error", "error": "StockManager MCP unavailable"})
            return
        payload = values.model_dump()
        catalog = await client.list_strategies_and_configs() or {}
        strategies = {str(item.get("name")): item for item in catalog.get("strategies", []) if isinstance(item, dict)}
        configs = {str(item.get("name")): item for item in catalog.get("configs", []) if isinstance(item, dict)}
        if values.strategy_name not in strategies or values.config_name not in configs:
            yield SkillEvent(event_type="skill_complete", data={"status": "error", "error": "Unknown MCP strategy or config"})
            return
        payload["strategy_sha1"] = strategies[values.strategy_name].get("sha1")
        payload["config_sha1"] = configs[values.config_name].get("sha1")
        backtest_id = backtest_request_id(payload)
        existing = db.get_backtest_run(backtest_id)
        if existing and existing["status"] in {"submitted", "running", "completed"}:
            yield SkillEvent(event_type="skill_complete", data={
                "status": "success", "backtest_id": backtest_id,
                "job_id": existing.get("job_id") or "", "backtest_status": existing["status"],
                "reused": True,
            })
            return
        yield SkillEvent(event_type="agent_status", data={"agent": "Backtest Runner", "status": "running"})
        result = await client.run_backtest(
            strategy=values.strategy_name, config=values.config_name,
            start=values.start_date, end=values.end_date,
            initial_cash=values.initial_cash, universe_size=values.universe_size,
        )
        if not isinstance(result, dict) or result.get("status") == "error":
            error = str((result or {}).get("error") if isinstance(result, dict) else "invalid MCP response")
            db.save_backtest_run(backtest_id=backtest_id, strategy_type=values.strategy_name,
                                 start_date=values.start_date, end_date=values.end_date,
                                 config=payload, status="failed", error=error)
            yield SkillEvent(event_type="skill_complete", data={"status": "error", "error": error})
            return
        job_id = str(result.get("job_id") or "")
        status = "submitted" if job_id else "completed"
        stored_result = {} if job_id else await audit_backtest_result(client, result, payload)
        db.save_backtest_run(backtest_id=backtest_id, job_id=job_id,
                             strategy_type=values.strategy_name, start_date=values.start_date,
                             end_date=values.end_date, config=payload, status=status,
                             result=stored_result)
        report = f"## 候选量化规则回测\n\n- 策略：{values.strategy_name}\n- 配置：{values.config_name}\n- 区间：{values.start_date} → {values.end_date}\n- 状态：{status}\n\n> 该结果验证 MCP 注册的量化规则，不等同于复现包含 LLM 复核的完整 DailyPipeline。"
        save_skill_artifact(
            config, skill_id="strategy_backtest", artifact_type="backtest_report",
            artifact_id=f"backtest:{backtest_id}", title="候选量化规则回测",
            subtitle=f"{values.start_date} → {values.end_date}",
            subject_type="strategy", subject_id=values.strategy_name,
            summary=f"回测状态：{status}", content_markdown=report,
            payload={"backtest_id": backtest_id, "job_id": job_id,
                     "status": status, "config": payload, "result": stored_result},
            tags=["strategy_backtest", "daily_pipeline"],
        )
        yield SkillEvent(event_type="report_chunk", data={"section": "strategy_backtest", "content": report, "is_final": True})
        yield SkillEvent(event_type="skill_complete", data={"status": "success", "backtest_id": backtest_id, "job_id": job_id, "backtest_status": status})

    async def cancel(self) -> None:
        return None
