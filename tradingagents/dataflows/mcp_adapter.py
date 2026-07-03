"""Adapters for StockManager MCP evidence payloads.

The MCP service returns structured JSON; TradingAgents agents mostly consume
compact markdown/evidence snippets.  Keep the shape conversion here so prompts
and skills do not learn vendor-specific response quirks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceCard:
    """Compact A-share evidence object passed into Agent prompts or reports."""

    ts_code: str
    as_of_date: str | None = None
    source: str | None = None
    adjustment: str | None = None
    industry: str | None = None
    tradability: dict[str, Any] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    factor_snapshot: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts_code": self.ts_code,
            "as_of_date": self.as_of_date,
            "source": self.source,
            "adjustment": self.adjustment,
            "industry": self.industry,
            "tradability": self.tradability,
            "risk_flags": self.risk_flags,
            "factor_snapshot": self.factor_snapshot,
            "warnings": self.warnings,
        }

    def to_markdown(self) -> str:
        lines = [f"### Evidence: {self.ts_code}"]
        if self.as_of_date:
            lines.append(f"- as_of_date: {self.as_of_date}")
        if self.source:
            lines.append(f"- source: {self.source}")
        if self.adjustment:
            lines.append(f"- adjustment: {self.adjustment}")
        if self.industry:
            lines.append(f"- industry: {self.industry}")
        if self.tradability:
            lines.append(f"- tradability: {_compact_dict(self.tradability)}")
        if self.risk_flags:
            lines.append(f"- risk_flags: {', '.join(self.risk_flags)}")
        if self.factor_snapshot:
            lines.append(f"- factors: {_compact_dict(self.factor_snapshot)}")
        if self.warnings:
            lines.append(f"- warnings: {'; '.join(self.warnings)}")
        return "\n".join(lines)


def evidence_from_mcp_payload(ts_code: str, payload: dict[str, Any] | None) -> EvidenceCard:
    """Build an EvidenceCard from a StockManager MCP response."""
    payload = payload or {}
    return EvidenceCard(
        ts_code=ts_code,
        as_of_date=payload.get("as_of_date") or payload.get("trade_date"),
        source=payload.get("source"),
        adjustment=payload.get("adj_type") or payload.get("adjustment"),
        industry=payload.get("industry"),
        tradability=payload.get("tradability") or {},
        risk_flags=list(payload.get("risk_flags") or []),
        factor_snapshot=payload.get("factor_snapshot") or {},
        warnings=list(payload.get("warnings") or []),
    )


def rows_to_markdown_table(title: str, rows: list[dict[str, Any]], limit: int = 20) -> str:
    """Render simple MCP row payloads as a markdown table."""
    if not rows:
        return f"### {title}\nNo rows returned."
    sample = rows[:limit]
    columns = list(sample[0].keys())
    lines = [f"### {title}", "| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in sample:
        lines.append("| " + " | ".join(_cell(row.get(col)) for col in columns) + " |")
    if len(rows) > limit:
        lines.append(f"\nShowing {limit} of {len(rows)} rows.")
    return "\n".join(lines)


def mcp_payload_to_markdown(title: str, payload: dict[str, Any] | None, limit: int = 20) -> str:
    """Render a StockManager MCP response into compact markdown."""
    if not payload:
        return f"### {title}\nMCP data unavailable."
    rows = payload.get("rows") or payload.get("panel") or payload.get("constituents") or []
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        body = rows_to_markdown_table(title, rows, limit=limit)
    elif isinstance(rows, list):
        body = f"### {title}\n" + "\n".join(f"- {item}" for item in rows[:limit])
    else:
        body = f"### {title}\n{payload}"

    meta = []
    for key in ("as_of_date", "source", "adj_type"):
        if payload.get(key):
            meta.append(f"{key}: {payload[key]}")
    if payload.get("warnings"):
        meta.append("warnings: " + "; ".join(str(w) for w in payload["warnings"]))
    return body + ("\n\n" + "\n".join(f"- {item}" for item in meta) if meta else "")


def payload_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return row objects from common StockManager MCP response shapes."""
    if not payload:
        return []
    rows = payload.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return [row for row in data["rows"] if isinstance(row, dict)]
    return []


def payload_warnings(payload: dict[str, Any] | None) -> list[str]:
    """Collect top-level warnings from a StockManager payload."""
    if not payload:
        return []
    warnings = [str(item) for item in payload.get("warnings") or []]
    data = payload.get("data")
    if isinstance(data, dict):
        warnings.extend(str(item) for item in data.get("warnings") or [])
    return warnings


def normalize_quant_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize MCP quant-rank rows into the Skill candidate shape."""
    symbol = str(row.get("ts_code") or row.get("symbol") or "").upper()
    factor_scores = row.get("factor_scores") if isinstance(row.get("factor_scores"), dict) else {}
    factor_snapshot = row.get("factor_snapshot")
    if not isinstance(factor_snapshot, dict):
        factor_snapshot = row.get("raw_factors") if isinstance(row.get("raw_factors"), dict) else {}
    tradability = row.get("tradability") if isinstance(row.get("tradability"), dict) else {}
    risk_flags = [str(item) for item in row.get("risk_flags") or []]
    score = _float_or(row.get("quant_score") or row.get("score"), 0.0)
    quant_decision = str(row.get("quant_decision") or row.get("decision") or _decision_from_score(score)).upper()
    data_coverage = row.get("data_coverage") if isinstance(row.get("data_coverage"), dict) else {}
    warnings = [str(item) for item in row.get("warnings") or []]
    if not data_coverage:
        warnings.append("data_coverage unavailable; neutral factor values may indicate missing MCP data.")
    return {
        "symbol": symbol,
        "ts_code": symbol,
        "name": str(row.get("name") or symbol),
        "industry": str(row.get("industry") or ""),
        "theme": str(row.get("industry") or row.get("theme") or "综合"),
        "rank": row.get("rank"),
        "board": row.get("board"),
        "score": round(score, 1),
        "quant_score": round(score, 1),
        "quant_decision": quant_decision,
        "quant_decision_reason": str(row.get("quant_decision_reason") or row.get("decision_reason") or ""),
        "quant_gate_reasons": row.get("gate_reasons") if isinstance(row.get("gate_reasons"), list) else [],
        "universe_percentile": row.get("universe_percentile"),
        "factor_scores": factor_scores,
        "factor_snapshot": factor_snapshot,
        "key_metrics": row.get("key_metrics") if isinstance(row.get("key_metrics"), dict) else {},
        "data_coverage": data_coverage,
        "factor_data_source": "stockmanager_mcp",
        "tradability": tradability,
        "risk_flags": risk_flags,
        "score_explain": [str(item) for item in row.get("score_explain") or []],
        "warnings": warnings,
    }


def _decision_from_score(score: float) -> str:
    if score >= 75:
        return "BUY"
    if score >= 60:
        return "WATCHLIST"
    if score >= 45:
        return "MONITOR"
    return "SKIP"


def _compact_dict(value: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in value.items())


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
