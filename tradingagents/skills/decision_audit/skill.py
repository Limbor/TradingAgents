from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.decision_audit import DecisionAuditEngine, audit_summary
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata


class DecisionAuditInput(BaseModel):
    as_of_date: str | None = None
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20])
    min_samples: int = Field(default=20, ge=5, le=500)
    limit: int = Field(default=20, ge=1, le=100)


class DecisionAuditOutput(BaseModel):
    evaluation: dict[str, Any]
    summary: dict[str, Any]


class DecisionAuditSkill(BaseSkill):
    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="decision_audit", name="Decision Audit",
            description="Evaluate due decision outcomes and report the decision-execution-return-reflection gate.",
            version="1.0.0", triggers=["决策审计", "评估到期决策", "策略表现", "decision audit"],
            icon="clipboard-check", category="review",
        )

    @property
    def input_schema(self):
        return DecisionAuditInput

    @property
    def output_schema(self):
        return DecisionAuditOutput

    async def execute(self, params: BaseModel, config: dict[str, Any]) -> AsyncIterator[SkillEvent]:
        values: DecisionAuditInput = params
        db = config["db"]
        yield SkillEvent(event_type="agent_status", data={"agent": "Decision Auditor", "status": "running"})
        evaluation = await DecisionAuditEngine(db, config).evaluate_due(
            as_of_date=values.as_of_date,
            horizons=tuple(sorted(set(values.horizons))),
            limit=values.limit,
        )
        summary = audit_summary(db, min_samples=values.min_samples)
        sample_ready = summary["validation"]["strategy_claims_allowed"]
        effectiveness = summary["validation"]["effectiveness_claim_allowed"]
        report = (
            "## 决策审计\n\n"
            f"- 决策：{summary['decision_count']}\n"
            f"- 已实现结果：{summary['realized_count']}\n"
            f"- 已关联真实执行：{summary['linked_execution_count']}\n"
            f"- 系统建议方向胜率：{summary['validation']['overall']['win_rate']:.1%}\n"
            f"- 用户实际执行胜率：{summary['execution_validation']['win_rate']:.1%}（n={summary['execution_validation']['sample_count']}）\n"
            f"- 本次新增收益快照：{evaluation['evaluated_outcomes']}\n"
            f"- 样本评估门禁：{'通过' if sample_ready else '样本不足'}\n"
            f"- 有效性声明门禁：{'通过' if effectiveness else '未通过，禁止宣称策略有效'}"
        )
        save_skill_artifact(
            config, skill_id="decision_audit", artifact_type="decision_audit_report",
            title="决策闭环审计", subtitle=f"已实现样本 {summary['realized_count']}",
            subject_type="system", subject_id="decision_audit",
            summary="有效性声明门禁通过" if effectiveness else "禁止策略有效性声明",
            content_markdown=report, payload={"evaluation": evaluation, "summary": summary},
            tags=["decision_audit", "governance"],
        )
        yield SkillEvent(event_type="report_chunk", data={"section": "decision_audit", "content": report, "is_final": True})
        yield SkillEvent(event_type="skill_complete", data={"status": "success", "evaluation": evaluation, "summary": summary})

    async def cancel(self) -> None:
        return None
