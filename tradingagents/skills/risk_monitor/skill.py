"""Portfolio risk monitor backed by StockManager MCP when available."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from tradingagents.core.artifacts import save_skill_artifact
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.persistence import Database
from tradingagents.core.trading_time import get_info_cutoff_date, get_temporal_context
from tradingagents.skills.base import BaseSkill, SkillEvent, SkillMetadata, skill_progress


# Severity-weighted risk classification. Previously the level was a pure count
# of matched announcements (<3 = orange, >=3 = red), which flagged a stock with
# three routine shareholder-减持 filings as "red" while a single 立案/违规
# announcement — far more serious — stayed "green". Keyword severity now drives
# the level; count is a secondary signal.
_RISK_KEYWORD_SEVERITY: dict[str, str] = {
    "立案": "red",
    "违规": "red",
    "处罚": "red",
    "退市": "red",
    "问询": "orange",
    "减持": "orange",
    "质押": "orange",
    "业绩预亏": "orange",
    "商誉减值": "orange",
}


def _classify_risk_rows(rows: list[Any], keywords: list[str]) -> tuple[str, str]:
    """Classify risk level from announcement rows by keyword severity.

    Returns ``(level, message)`` where level is one of green/orange/red. The
    highest severity keyword found across all rows wins; if only generic
    keywords (no mapped severity) match, the count-based fallback applies.
    Duplicate announcements (same title) are de-duplicated before counting.
    """
    if not rows:
        return "green", "No matched risk announcements."

    seen_titles: set[str] = set()
    unique_rows: list[Any] = []
    for row in rows:
        row_dict = row if isinstance(row, dict) else {}
        title = str(row_dict.get("title") or row_dict.get("公告标题") or row_dict.get("subject") or "")
        if title and title in seen_titles:
            continue
        seen_titles.add(title)
        unique_rows.append(row)

    worst = "green"
    for row in unique_rows:
        # Scan the row's string representation so we catch title/content/any field.
        row_text = str(row)
        for kw, severity in _RISK_KEYWORD_SEVERITY.items():
            if kw in row_text:
                if severity == "red":
                    return "red", f"Critical risk keyword '{kw}' matched in announcements."
                if severity == "orange" and worst != "red":
                    worst = "orange"

    if worst == "orange":
        return "orange", f"{len(unique_rows)} matched risk announcements (moderate severity keywords)."
    # No mapped severity keyword — fall back to count-based grading on unique rows.
    if len(unique_rows) >= 3:
        return "red", f"{len(unique_rows)} matched risk announcements in the lookback window."
    return "orange" if unique_rows else "green", f"{len(unique_rows)} matched risk announcements in the lookback window."


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
        temporal_context = get_temporal_context(config, market="cn_a")

        yield SkillEvent(
            event_type="skill_start",
            data={
                "skill_id": self.metadata.id,
                "holding_count": len(holdings),
                "temporal_context": temporal_context.to_dict(),
            },
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
        report = _render_report(holdings, risks, mcp_used)
        _save_risk_artifact(config, input_params, holdings, risks, mcp_used, report)

        yield SkillEvent(
            event_type="report_chunk",
            data={
                "section": "risk_monitor_report",
                "content": report,
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
                "market_asof_date": temporal_context.market_asof_date,
                "info_cutoff": temporal_context.info_cutoff,
                "temporal_context": temporal_context.to_dict(),
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
    end = get_info_cutoff_date(config, market="cn_a")
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
        level, message = _classify_risk_rows(rows, params.keywords)
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


def _save_risk_artifact(
    config: dict[str, Any],
    input_params: RiskMonitorInput,
    holdings: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    mcp_used: bool,
    report: str,
) -> None:
    actionable = [item for item in risks if item.get("level") not in {"green", "unknown"}]
    high = [item for item in risks if item.get("level") in {"red", "critical", "high"}]
    save_skill_artifact(
        config,
        skill_id="risk_monitor",
        artifact_type="risk_report",
        title="持仓风险扫描",
        subtitle=f"{len(holdings)} 个持仓 · {input_params.lookback_days} 天",
        subject_type="portfolio",
        subject_id="default",
        subject_name="当前持仓",
        status="success",
        summary=f"扫描 {len(holdings)} 个持仓，发现 {len(actionable)} 个风险项，高风险 {len(high)} 个",
        content_markdown=report,
        payload={
            "holdings": holdings,
            "risks": risks,
            "mcp_used": mcp_used,
            "lookback_days": input_params.lookback_days,
            "keywords": input_params.keywords,
        },
        tags=["risk_monitor", "portfolio"],
    )


skill = RiskMonitorSkill()
