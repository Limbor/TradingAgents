"""Portfolio risk monitor backed by StockManager MCP when available."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.persistence import Database
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress


class RiskMonitorInput(BaseModel):
    lookback_days: int = Field(default=30, ge=1, le=365)
    keywords: list[str] = Field(default_factory=lambda: ["立案", "问询", "违规", "处罚", "减持"])


class RiskMonitorOutput(BaseModel):
    holdings: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    mcp_used: bool


class RiskMonitorSkill(BaseSkill):
    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            id="risk_monitor",
            name="Risk Monitor",
            description="Scan tracked holdings for A-share announcements and portfolio risk flags.",
            version="1.0.0",
            triggers=["risk monitor", "风险监控", "持仓风险", "预警", "风险扫描"],
            icon="shield-alert",
            category="portfolio",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return RiskMonitorInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return RiskMonitorOutput

    async def execute(self, params: BaseModel, config: dict[str, Any]) -> AsyncIterator[SkillEvent]:
        input_params: RiskMonitorInput = params
        db = config.get("db") or Database()
        holdings = db.list_holdings()

        yield SkillEvent(
            event_type="skill_start",
            data={"skill_id": self.metadata.id, "holding_count": len(holdings)},
        )
        yield skill_progress(
            stage_id="prepare",
            stage_label="读取持仓",
            status="completed",
            detail=f"{len(holdings)} 个持仓",
            progress_pct=10,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Risk Monitor",
                "status": f"正在读取 {len(holdings)} 个持仓并准备公告扫描",
            },
        )
        yield skill_progress(
            stage_id="announcement_scan",
            stage_label="公告与风险事件扫描",
            status="running",
            detail=f"回看 {input_params.lookback_days} 天，关键词 {len(input_params.keywords)} 个",
            agent="Risk Monitor",
            progress_pct=35,
        )

        risks, mcp_used = await _scan_risks(holdings, input_params, config)
        yield skill_progress(
            stage_id="announcement_scan",
            stage_label="公告与风险事件扫描",
            status="completed",
            detail=f"发现 {len([item for item in risks if item.get('level') not in {'green', 'unknown'}])} 个风险项",
            agent="Risk Monitor",
            progress_pct=70,
        )
        yield SkillEvent(
            event_type="agent_status",
            data={
                "agent": "Announcement Scanner",
                "status": "正在汇总风险公告、问询函、处罚和减持线索",
            },
        )
        yield skill_progress(
            stage_id="risk_summary",
            stage_label="风险汇总",
            status="running",
            detail="正在生成持仓风险摘要",
            agent="Announcement Scanner",
            progress_pct=85,
        )
        yield SkillEvent(
            event_type="risk_monitor_results",
            data={"holdings": holdings, "risks": risks, "mcp_used": mcp_used},
        )
        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "risk_monitor_report",
                "content": _render_report(holdings, risks, mcp_used),
                "is_final": True,
            },
        )
        yield skill_progress(
            stage_id="risk_summary",
            stage_label="风险汇总",
            status="completed",
            detail="风险报告已生成",
            agent="Announcement Scanner",
            progress_pct=100,
        )
        yield SkillEvent(
            event_type="skill_complete",
            data={
                "status": "success",
                "holdings": holdings,
                "risks": risks,
                "mcp_used": mcp_used,
            },
        )

    async def cancel(self) -> None:
        return None


async def _scan_risks(
    holdings: list[dict[str, Any]],
    params: RiskMonitorInput,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    client = await get_mcp_client(config)
    end = date.today()
    start = end - timedelta(days=params.lookback_days)
    risks = []

    if client is None:
        for item in holdings:
            risks.append(
                {
                    "symbol": item["symbol"],
                    "level": "unknown",
                    "message": "StockManager MCP unavailable; announcement scan skipped.",
                    "announcements": [],
                }
            )
        return risks, False

    for item in holdings:
        symbol = str(item.get("symbol") or "").upper()
        try:
            payload = await client.get_risk_announcements(
                symbol,
                start.isoformat(),
                end.isoformat(),
                keywords=params.keywords,
            )
        except Exception as exc:
            logger.warning("Risk scan failed for %s: %s", symbol, exc)
            risks.append(
                {
                    "symbol": symbol,
                    "level": "unknown",
                    "message": f"Risk scan error: {exc}",
                    "announcements": [],
                }
            )
            continue
        if not isinstance(payload, dict):
            payload = {}
        rows = payload.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        level = "green"
        message = "No matched risk announcements."
        if rows:
            level = "orange" if len(rows) < 3 else "red"
            message = f"{len(rows)} matched risk announcements in the lookback window."
        risks.append(
            {
                "symbol": symbol,
                "level": level,
                "message": message,
                "announcements": rows,
                "warnings": payload.get("warnings", []),
            }
        )
    return risks, True


def _render_report(holdings: list[dict[str, Any]], risks: list[dict[str, Any]], mcp_used: bool) -> str:
    if not holdings:
        return "## Risk Monitor\n\nNo holdings are currently tracked."

    lines = [
        "## Risk Monitor",
        "",
        f"- Source: **{'StockManager MCP' if mcp_used else 'degraded mode'}**",
        f"- Holdings scanned: **{len(holdings)}**",
        "",
        "| Symbol | Level | Message |",
        "|---|---|---|",
    ]
    for item in risks:
        lines.append(f"| {item['symbol']} | {item['level']} | {item['message']} |")
    return "\n".join(lines)


skill = RiskMonitorSkill()
