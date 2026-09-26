"""Lightweight tool handlers for the Free ChatAgent.

Each handler is an async function that receives validated keyword arguments
and returns a JSON-serialisable dict. Handlers MUST catch all exceptions
internally and return ``{"error": "...", "warnings": [...]}`` on failure.

These are registered with ToolRegistry during app startup (see app.py lifespan).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def paper_ledger_conflicts(expected_session_id: str, result: dict[str, Any]) -> list[str]:
    """Reject account or ledger-date contradictions before using paper evidence."""
    conflicts: list[str] = []
    session = result.get("session") if isinstance(result.get("session"), dict) else {}
    snapshot = result.get("snapshot") if isinstance(result.get("snapshot"), dict) else {}
    for actual in (result.get("session_id"), session.get("session_id")):
        if actual and actual != expected_session_id:
            conflicts.append("账本返回的模拟盘账户与当前对话绑定账户不一致")
            break

    dates: list[str] = []
    for value in (result.get("as_of_date"), snapshot.get("as_of_date"), session.get("last_date")):
        if value is None or value == "":
            continue
        normalized = str(value)[:10]
        try:
            date.fromisoformat(normalized)
        except ValueError:
            conflicts.append("账本基准日期格式无效")
            break
        dates.append(normalized)
    if len(set(dates)) > 1:
        conflicts.append("账本快照日期与会话日期不一致")
    return conflicts


def make_get_paper_session(config: dict[str, Any]):
    """Read current strategy-paper evidence from StockManager for chat."""
    async def _handler(session_id: str = "") -> dict[str, Any]:
        import re

        from tradingagents.core.stockmanager_paper import PaperServiceError, paper_request

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", session_id) or ".." in session_id:
            return {"error": "有效的模拟盘 session_id 必填", "warnings": []}
        root = f"/api/v2/paper/{session_id}"
        try:
            status = (await paper_request(config, "GET", root + "/status")).get("data") or {}
        except PaperServiceError as exc:
            return {"error": str(exc), "warnings": ["StockManager 模拟盘不可用"]}
        conflicts = paper_ledger_conflicts(session_id, status)
        if conflicts:
            return {"error": "；".join(conflicts), "warnings": conflicts,
                    "session_id": session_id, "source": "StockManager strategy paper ledger"}
        from asyncio import gather

        plan_result, trades_result, curve_result = await gather(
            paper_request(config, "GET", root + "/next_plan"),
            paper_request(config, "GET", root + "/trades?limit=30"),
            paper_request(config, "GET", root + "/equity_curve"),
            return_exceptions=True,
        )
        warnings: list[str] = []
        for label, result in (
            ("下一日计划", plan_result),
            ("近期成交", trades_result),
            ("净值曲线", curve_result),
        ):
            if isinstance(result, BaseException):
                warnings.append(f"{label}不可用: {result}")
        plan = plan_result.get("data") if isinstance(plan_result, dict) else None
        trades = trades_result.get("items", []) if isinstance(trades_result, dict) else []
        curve = curve_result.get("data") if isinstance(curve_result, dict) else None
        snapshot = status.get("snapshot") or {}
        if status.get("caveat"):
            warnings.append(status["caveat"])
        return {
            "session_id": session_id,
            "as_of_date": snapshot.get("as_of_date") or status.get("session", {}).get("last_date"),
            "source": "StockManager strategy paper ledger",
            "session": status.get("session"),
            "snapshot": snapshot,
            "decision": status.get("decision"),
            "recent_decisions": (status.get("decisions") or [])[-5:],
            "readiness": status.get("readiness"),
            "freshness": status.get("freshness"),
            "sleeves": status.get("sleeves"),
            "summary": status.get("summary"),
            "next_plan": plan,
            "recent_trades": trades[:30],
            "equity_tail": (curve.get("daily_records") or [])[-30:] if isinstance(curve, dict) else [],
            "warnings": warnings,
        }

    return _handler


# ---------------------------------------------------------------------------
# Handler factories — each returns an async handler with the right signature
# for ToolRegistry. Closures capture db / config at startup; the MCP client is
# fetched at call time via get_mcp_client(config) so URL changes take effect.
# ---------------------------------------------------------------------------


def make_get_portfolio_summary(db: Any):
    """Build handler: get_portfolio_summary — current holdings + P&L.

    Returns a summary of all holdings including cost, current_price (if
    available), and estimated P&L.
    """
    async def _handler() -> dict[str, Any]:
        try:
            holdings = db.list_holdings()
        except Exception as exc:
            return {"error": f"Failed to load holdings: {exc}", "warnings": []}

        if not holdings:
            return {
                "holdings": [],
                "total_symbols": 0,
                "message": "No holdings found.",
            }

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for h in holdings:
            cost = h.get("cost_basis")
            current = h.get("current_price")
            quantity = h.get("quantity", 0)
            pnl = None
            if cost is not None and current is not None and quantity:
                try:
                    pnl = round(float(quantity) * (float(current) - float(cost)), 2)
                except (TypeError, ValueError):
                    warnings.append(f"Cannot compute P&L for {h.get('symbol', '?')}")

            rows.append({
                "symbol": h.get("symbol", ""),
                "name": h.get("name", ""),
                "quantity": quantity,
                "cost_basis": cost,
                "current_price": current,
                "pnl": pnl,
                "sector": h.get("sector", ""),
                "weight_pct": h.get("weight_pct"),
            })

        return {
            "holdings": rows,
            "total_symbols": len(rows),
            "warnings": warnings,
        }

    return _handler


def make_search_artifacts(db: Any):
    """Build handler: search_artifacts — search Library artifacts by keyword.

    Args:
        q (str): Search query (title, summary, subject_id, subject_name).
        limit (int, default 10): Max results to return.
    """

    async def _handler(q: str = "", limit: int = 10) -> dict[str, Any]:
        try:
            results = db.list_artifacts(q=q, limit=min(limit, 50))
        except Exception as exc:
            return {"error": f"Failed to search artifacts: {exc}", "warnings": []}

        if not results:
            return {"results": [], "query": q, "message": f"No artifacts found for '{q}'."}

        return {
            "results": [
                {
                    "id": a.get("id", ""),
                    "title": a.get("title", ""),
                    "summary": a.get("summary", ""),
                    "artifact_type": a.get("artifact_type", ""),
                    "subject_id": a.get("subject_id", ""),
                    "subject_name": a.get("subject_name", ""),
                    "created_at": a.get("created_at", ""),
                }
                for a in results
            ],
            "query": q,
            "total": len(results),
        }

    return _handler


def make_get_recent_runs(db: Any):
    """Build handler: get_recent_runs — recent skill run statuses.

    Args:
        limit (int, default 10): Max runs to return.
    """

    async def _handler(limit: int = 10) -> dict[str, Any]:
        try:
            runs = db.list_runs(limit=min(limit, 50))
        except Exception as exc:
            return {"error": f"Failed to load runs: {exc}", "warnings": []}

        if not runs:
            return {"runs": [], "message": "No runs found."}

        return {
            "runs": [
                {
                    "id": r.get("id", ""),
                    "skill_id": r.get("skill_id", ""),
                    "status": r.get("status", ""),
                    "params_json": r.get("params_json", ""),
                    "created_at": r.get("created_at", ""),
                    "updated_at": r.get("updated_at", ""),
                }
                for r in runs
            ],
            "total": len(runs),
        }

    return _handler


def make_get_mcp_factor_snapshot(config: dict[str, Any]):
    """Build handler: get_mcp_factor_snapshot — MCP factor snapshot for a symbol.

    The MCP client is fetched at call time via ``get_mcp_client(config)``
    rather than captured at startup, so a Settings MCP-URL change (which
    resets the singleton) is picked up without restarting the app.

    Args:
        ts_code (str): Trading symbol code, e.g. ``000967.SZ``.
        trade_date (str, optional): Override trade date (YYYY-MM-DD).

    Returns factor valuation, momentum, quality, and flow data with
    as_of_date, source, and warnings metadata.
    """

    async def _handler(ts_code: str = "", trade_date: str = "") -> dict[str, Any]:
        if not ts_code:
            return {"error": "ts_code is required", "warnings": []}

        # Fetch the current MCP client on each call so URL changes take effect.
        from tradingagents.core.mcp_client import get_mcp_client

        mcp_client = await get_mcp_client(config)
        if mcp_client is None:
            return {
                "error": "StockManager MCP is not connected",
                "warnings": ["MCP client unavailable — try again later or check StockManager service"],
            }

        try:
            from tradingagents.core.trading_time import get_temporal_context

            if not trade_date:
                ctx = get_temporal_context(config, market="cn_a")
                trade_date = ctx.market_asof_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            result = await mcp_client.get_factor_snapshot(
                ts_codes=[ts_code],
                trade_date=trade_date,
                lookback_days=120,
            )
        except Exception as exc:
            return {
                "error": f"MCP factor_snapshot failed: {exc}",
                "warnings": ["StockManager MCP call failed"],
            }

        if result is None:
            return {
                "error": f"No factor snapshot data returned for {ts_code}",
                "warnings": ["MCP returned None — session may have timed out"],
            }

        rv: dict[str, Any] = {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "as_of_date": trade_date,
            "source": "StockManager MCP (get_factor_snapshot)",
        }

        if isinstance(result, dict):
            rv["snapshot"] = result

        return rv

    return _handler


def make_get_strategy_lessons(db: Any):
    """Build handler: get_strategy_lessons — list active strategy lessons.

    Returns recent reflection-derived lessons with findings and suggested
    adjustments.
    """

    async def _handler() -> dict[str, Any]:
        try:
            lessons = db.list_strategy_lessons(limit=20, active_only=True)
        except Exception as exc:
            return {"error": f"Failed to load strategy lessons: {exc}", "warnings": []}

        if not lessons:
            return {"lessons": [], "message": "No active strategy lessons yet."}

        return {
            "lessons": [
                {
                    "id": lesson.get("id", ""),
                    "lesson_type": lesson.get("lesson_type", ""),
                    "scope": lesson.get("scope", ""),
                    "target": lesson.get("target", ""),
                    "finding": lesson.get("finding", ""),
                    "suggested_adjustment": lesson.get("suggested_adjustment", ""),
                    "confidence": lesson.get("confidence", ""),
                    "evidence_count": lesson.get("evidence_count", 0),
                    "created_at": lesson.get("created_at", ""),
                }
                for lesson in lessons
            ],
            "total": len(lessons),
        }

    return _handler


# ---------------------------------------------------------------------------
# Tool definitions — metadata used to create LightweightTool instances
# These are built at startup in app.py lifespan after db/mcp are ready.
# ---------------------------------------------------------------------------


def build_all_tools(
    db: Any,
    mcp_client: Any | None,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a list of tool definition dicts ready for ToolRegistry.

    Each dict has: name, description, parameters (JSON Schema), handler, display.
    Call ``ToolRegistry.register(LightweightTool(**d))`` in the app lifespan.
    """
    return [
        {
            "name": "get_paper_session",
            "description": (
                "Read a StockManager strategy paper trading session: authoritative equity, "
                "cash, positions, allocator decision, next plan and recent trades. "
                "Use for questions about a paper session, its performance, trades, or why it switched. "
                "The session_id is in paper_session_context when opened from the paper page."
            ),
            "parameters": {
                "type": "object",
                "properties": {"session_id": {"type": "string", "description": "Paper session ID"}},
                "required": ["session_id"],
            },
            "handler": make_get_paper_session(config),
            "display": "card",
        },
        {
            "name": "get_portfolio_summary",
            "description": (
                "Get current portfolio holdings summary including symbols, quantities, "
                "cost basis, current prices, and estimated P&L. Use when the user asks "
                "about their positions, holdings, portfolio status, or P&L."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
            "handler": make_get_portfolio_summary(db),
            "display": "table",
        },
        {
            "name": "search_artifacts",
            "description": (
                "Search Library artifacts (reports, analyses, reflections) by keyword. "
                "Searches title, summary, subject_id, and subject_name. Use when the user "
                "asks about past analysis results, recommendations, or reports."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Search keyword for artifacts (title/summary/subject match).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of results (default 10, max 50).",
                        "default": 10,
                    },
                },
                "required": ["q"],
            },
            "handler": make_search_artifacts(db),
            "display": "card",
        },
        {
            "name": "get_recent_runs",
            "description": (
                "List recent skill run statuses (e.g. daily pipeline, stock analysis, "
                "risk monitoring). Use when the user asks about recent task status, "
                "what tasks ran, or the outcome of recent runs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of runs to return (default 10, max 50).",
                        "default": 10,
                    },
                },
            },
            "handler": make_get_recent_runs(db),
            "display": "table",
        },
        {
            "name": "get_mcp_factor_snapshot",
            "description": (
                "Get factor snapshot (valuation, momentum, quality, flow indicators) "
                "for a specific stock via the StockManager MCP service. Use when the user "
                "asks about a stock's current valuation, capital flow, or technical indicators. "
                "Requires a valid trading symbol code like '000967.SZ'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ts_code": {
                        "type": "string",
                        "description": "Trading symbol code, e.g. '000967.SZ' or '600036.SH'.",
                    },
                    "trade_date": {
                        "type": "string",
                        "description": "Trade date in YYYY-MM-DD format (optional, defaults to latest trading day).",
                    },
                },
                "required": ["ts_code"],
            },
            "handler": make_get_mcp_factor_snapshot(config),
            "display": "card",
        },
        {
            "name": "get_strategy_lessons",
            "description": (
                "List active strategy lessons from the reflection engine. Each lesson "
                "contains a finding (pattern discovered) and a suggested adjustment. Use "
                "when the user asks about lessons learned, recent reflections, or strategy improvements."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
            "handler": make_get_strategy_lessons(db),
            "display": "text",
        },
    ]
