"""Lightweight tool handlers for the Free ChatAgent.

Each handler is an async function that receives validated keyword arguments
and returns a JSON-serialisable dict. Handlers MUST catch all exceptions
internally and return ``{"error": "...", "warnings": [...]}`` on failure.

These are registered with ToolRegistry during app startup (see app.py lifespan).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

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


def _paper_plan_current(plan: Any, ledger_date: Any) -> bool:
    """A next-day plan must be based on the account's current ledger date."""
    if not isinstance(plan, dict) or plan.get("error") or not ledger_date:
        return False
    signal_date = plan.get("signal_date")
    if not signal_date:
        return False
    try:
        return date.fromisoformat(str(signal_date)[:10]) == date.fromisoformat(
            str(ledger_date)[:10]
        )
    except ValueError:
        return False


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
        ledger_date = snapshot.get("as_of_date") or status.get("session", {}).get("last_date")
        freshness = status.get("freshness")
        freshness = dict(freshness) if isinstance(freshness, dict) else {}
        plan_current = _paper_plan_current(plan, ledger_date)
        if not plan_current:
            freshness["is_active_plan_current"] = False
            warnings.append("下一日计划缺失、出错或基准日与账本不一致，不能作为当前交易依据")
        elif freshness.get("is_active_plan_current") is False:
            warnings.append("StockManager 标记下一日计划不是最新版本，不能作为当前交易依据")
        else:
            freshness["is_active_plan_current"] = True
        if status.get("caveat"):
            warnings.append(status["caveat"])
        return {
            "session_id": session_id,
            "as_of_date": ledger_date,
            "source": "StockManager strategy paper ledger",
            "session": status.get("session"),
            "snapshot": snapshot,
            "state_fingerprint": status.get("state_fingerprint"),
            "decision": status.get("decision"),
            "recent_decisions": (status.get("decisions") or [])[-5:],
            "readiness": status.get("readiness"),
            "freshness": freshness,
            "sleeves": status.get("sleeves"),
            "summary": status.get("summary"),
            "next_plan": plan,
            "trades_count": status.get("trades_count"),
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
                "source": "TradingAgents local holdings",
            }

        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for h in holdings:
            cost = h.get("cost_basis")
            if cost is None:
                cost = h.get("avg_cost")
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
                "record_updated_at": h.get("updated_at"),
            })

        if any(row["current_price"] is not None for row in rows):
            warnings.append("持仓价格为本地保存值，未核对实时行情或价格时点")

        return {
            "holdings": rows,
            "total_symbols": len(rows),
            "source": "TradingAgents local holdings",
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

        source = "StockManager MCP (get_factor_snapshot)"
        if not isinstance(result, dict):
            return {"error": "StockManager 因子快照格式无效", "warnings": [],
                    "ts_code": ts_code, "source": source}
        warnings = ([str(item)[:500] for item in result["warnings"][:10]]
                    if isinstance(result.get("warnings"), list) else [])
        if result.get("status") == "error":
            remote_error = result.get("error")
            message = (result.get("message") or
                       (remote_error.get("message") if isinstance(remote_error, dict) else remote_error) or
                       "StockManager 因子快照不可用")
            return {"error": str(message)[:500], "warnings": warnings,
                    "ts_code": ts_code, "source": source}
        rows = result.get("rows") if isinstance(result.get("rows"), list) else []
        matching = [row for row in rows if isinstance(row, dict) and
                    str(row.get("ts_code") or "").upper() == ts_code.upper()]
        if result.get("status") not in {"success", "partial"} or not matching:
            return {"error": "StockManager 未返回当前标的的有效因子快照",
                    "warnings": warnings, "ts_code": ts_code, "source": source}
        as_of_date = str(result.get("as_of_date") or "")[:10]
        row_date = str(matching[0].get("trade_date") or as_of_date)[:10]
        if as_of_date != trade_date or row_date != trade_date:
            return {"error": "因子快照基准日与请求交易日不一致",
                    "warnings": warnings, "ts_code": ts_code, "source": source}
        raw_factors = matching[0].get("raw_factors")
        if not isinstance(raw_factors, dict) or not any(
            value is not None for value in raw_factors.values()
        ):
            return {"error": "当前标的缺少可用的原始因子数据",
                    "warnings": warnings, "ts_code": ts_code, "source": source}
        if result.get("status") == "partial":
            warnings = [*warnings, "因子快照覆盖不完整，缺失维度不能当作中性分"]
        row_warnings = matching[0].get("warnings")
        if isinstance(row_warnings, list):
            warnings.extend(str(item)[:500] for item in row_warnings[:10])
        tradability = matching[0].get("tradability")
        if isinstance(tradability, dict) and tradability.get("is_tradable") is False:
            reason = str(tradability.get("reason") or "未提供原因")[:100]
            warnings.append(f"{trade_date} 的可交易性检查未通过或资料不足：{reason}；不能据此判断可成交")
        coverage = matching[0].get("data_coverage")
        if isinstance(coverage, dict):
            missing = sorted(str(name) for name, state in coverage.items()
                             if state == "missing")
            if missing:
                warnings.append(
                    f"因子维度缺失：{', '.join(missing)}；对应默认分数只是占位值，不能解释为中性或有效信号"
                )

        rv: dict[str, Any] = {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "as_of_date": result.get("as_of_date") or trade_date,
            "source": source,
            "warnings": warnings,
            "snapshot": result,
        }

        return rv

    return _handler


def make_get_mcp_risk_announcements(config: dict[str, Any]):
    """Read keyword-hit dates only; this MCP response is not an announcement feed."""
    async def _handler(ts_code: str = "", end_date: str = "") -> dict[str, Any]:
        code = ts_code.upper() if isinstance(ts_code, str) else ""
        if not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", code):
            return {"error": "有效的 A 股 ts_code 必填", "warnings": []}
        if not end_date:
            try:
                from tradingagents.core.trading_time import get_temporal_context

                context = get_temporal_context(config, market="cn_a")
                end_date = context.market_asof_date or datetime.now(timezone.utc).date().isoformat()
            except Exception:
                end_date = datetime.now(timezone.utc).date().isoformat()
        try:
            end = date.fromisoformat(end_date)
        except (TypeError, ValueError):
            return {"error": "公告查询截止日期格式无效", "warnings": [], "ts_code": code}
        if end > datetime.now(ZoneInfo("Asia/Shanghai")).date():
            return {"error": "公告查询截止日期不能晚于当前日期", "warnings": [], "ts_code": code}
        start = end - timedelta(days=90)

        from tradingagents.core.mcp_client import get_mcp_client

        client = await get_mcp_client(config)
        if client is None:
            return {"error": "StockManager MCP 未连接", "warnings": ["风险公告查询不可用"],
                    "ts_code": code}
        try:
            payload = await client.get_risk_announcements(
                code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
                keywords=["立案", "问询", "违规", "处罚", "退市", "减持", "预亏"],
            )
        except Exception as exc:
            return {"error": f"风险公告查询失败：{exc}", "warnings": [], "ts_code": code}
        if not isinstance(payload, dict) or payload.get("status") == "error":
            message = payload.get("message") if isinstance(payload, dict) else None
            return {"error": str(message or "风险公告结果不可用")[:500],
                    "warnings": [], "ts_code": code}
        if payload.get("ts_code") and str(payload["ts_code"]).upper() != code:
            return {"error": "风险公告返回标的与请求不一致", "warnings": [], "ts_code": code}
        if not isinstance(payload.get("rows"), list):
            return {"error": "风险公告结果缺少有效 rows", "warnings": [], "ts_code": code}
        rows: list[dict[str, str]] = []
        for row in payload["rows"]:
            if not isinstance(row, dict):
                return {"error": "风险公告行格式无效", "warnings": [], "ts_code": code}
            if row.get("ts_code") and str(row["ts_code"]).upper() != code:
                return {"error": "风险公告行标的与请求不一致", "warnings": [], "ts_code": code}
            raw_date = str(row.get("ann_date") or "")
            try:
                observed = date.fromisoformat(
                    f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                    if re.fullmatch(r"\d{8}", raw_date) else raw_date
                )
            except ValueError:
                return {"error": "风险公告日期格式无效", "warnings": [], "ts_code": code}
            if not start <= observed <= end:
                return {"error": "风险公告日期超出查询区间", "warnings": [], "ts_code": code}
            rows.append({"ann_date": observed.isoformat(),
                         "keyword": str(row.get("keyword") or "matched")[:40]})
        rows.sort(key=lambda row: row["ann_date"], reverse=True)
        warnings = ([str(item)[:500] for item in payload["warnings"][:10]]
                    if isinstance(payload.get("warnings"), list) else [])
        if payload.get("status") == "partial":
            warnings.append("风险公告数据覆盖不完整，不能将结果视为完整公告清单")
        warnings.append("仅返回风险关键词命中日期，不含公告标题或原文，不能据此判断事件性质或严重程度")
        if not rows:
            warnings.append("查询区间内未返回关键词命中，不代表没有其他风险公告")
        if len(rows) > 20:
            warnings.append("命中日期较多，仅展示前 20 条；总数保留在证据中")
        return {"ts_code": code, "start_date": start.isoformat(),
                "end_date": end.isoformat(), "as_of_date": end.isoformat(),
                "source": "StockManager MCP (get_risk_announcements)",
                "rows": rows[:20], "count": len(rows), "warnings": warnings}

    return _handler


def make_get_strategy_lessons(db: Any):
    """Retrieve governed historical lessons for the current task context."""
    async def _handler(symbol: str = "", industries: list[str] | None = None,
                       board: str = "", factors: list[str] | None = None,
                       style: str = "", regime: str = "", task_type: str = "",
                       as_of_date: str | None = None, limit: int = 5) -> dict[str, Any]:
        from tradingagents.core.strategy_memory import (
            load_strategy_lessons,
            select_strategy_lessons,
        )

        cutoff = as_of_date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        try:
            date.fromisoformat(cutoff)
            context = {"symbol": symbol, "industries": industries or [], "board": board,
                       "factors": factors or [], "style": style, "regime": regime, "task_type": task_type}
            lessons = select_strategy_lessons(load_strategy_lessons(db, cutoff), context,
                                              as_of_date=cutoff, limit=limit)
        except Exception as exc:
            return {"error": f"Failed to retrieve strategy lessons: {exc}", "warnings": []}
        return {"lessons": lessons, "total": len(lessons), "memory_cutoff": cutoff,
                "context": context, "kind": "historical_memory",
                "source": "TradingAgents reflection store",
                "warnings": ["历史经验仅作条件参考，不代表当前行情或已验证的收益改善"],
                "message": "No applicable approved lessons." if not lessons else ""}

    return _handler


# ---------------------------------------------------------------------------
# Tool definitions — metadata used to create LightweightTool instances
# These are built at startup in app.py lifespan after db/mcp are ready.
# ---------------------------------------------------------------------------


def _result_schema(**fields: str | list[str]) -> dict[str, Any]:
    return {"type": "object", "required": list(fields),
            "properties": {name: {"type": kind} for name, kind in fields.items()}}


def build_all_tools(
    db: Any,
    mcp_client: Any | None,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a list of tool definition dicts ready for ToolRegistry.

    Each dict declares input/output schemas, permission, scope, timeout and retry policy.
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
            "output_schema": _result_schema(session_id="string", as_of_date=["string", "null"],
                                            snapshot="object"),
            "permission": "read", "scope": "paper", "timeout_seconds": 100.0,
            "data_source": "StockManager Web",
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
            "output_schema": _result_schema(holdings="array", total_symbols="integer"),
            "permission": "read", "scope": "global", "timeout_seconds": 10.0,
            "data_source": "TradingAgents local holdings",
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
            "output_schema": _result_schema(results="array", query="string"),
            "permission": "read", "scope": "global", "timeout_seconds": 15.0,
            "data_source": "TradingAgents Library",
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
            "output_schema": _result_schema(runs="array"),
            "permission": "read", "scope": "global", "timeout_seconds": 10.0,
            "data_source": "TradingAgents RunManager",
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
            "output_schema": _result_schema(ts_code="string", as_of_date="string",
                                            snapshot="object"),
            "permission": "read", "scope": "symbol", "timeout_seconds": 45.0,
            "retry_policy": "date_conflict_once",
            "data_source": "StockManager MCP",
        },
        {
            "name": "get_mcp_risk_announcements",
            "description": (
                "Read risk-keyword announcement hit dates for an explicit A-share symbol "
                "within a 90-day window via StockManager MCP. Returns dates, not titles, "
                "full text, or severity. Use for questions about announcements, inquiries, "
                "investigations, violations, or delisting risk."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ts_code": {"type": "string", "description": "Explicit A-share symbol code."},
                    "end_date": {"type": "string", "description": "Query cutoff YYYY-MM-DD."},
                },
                "required": ["ts_code"],
            },
            "handler": make_get_mcp_risk_announcements(config),
            "display": "card",
            "output_schema": _result_schema(ts_code="string", as_of_date="string",
                                            rows="array", count="integer"),
            "permission": "read", "scope": "symbol", "timeout_seconds": 45.0,
            "retry_policy": "date_conflict_once",
            "data_source": "StockManager MCP",
        },
        {
            "name": "get_strategy_lessons",
            "description": (
                "Retrieve up to five relevant approved historical lessons for this task, "
                "ranked by symbol, industry, board, factor and reliability. Supply the "
                "current task context before making an analysis; these lessons are not current market evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "maxLength": 32},
                    "industries": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 10},
                    "board": {"type": "string", "maxLength": 40},
                    "factors": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 10},
                    "style": {"type": "string", "maxLength": 40},
                    "regime": {"type": "string", "maxLength": 40},
                    "task_type": {"type": "string", "maxLength": 40},
                    "as_of_date": {"type": "string", "format": "date"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "additionalProperties": False,
            },
            "handler": make_get_strategy_lessons(db),
            "display": "text",
            "output_schema": _result_schema(lessons="array"),
            "permission": "read", "scope": "global", "timeout_seconds": 10.0,
            "data_source": "TradingAgents reflection store",
        },
    ]
