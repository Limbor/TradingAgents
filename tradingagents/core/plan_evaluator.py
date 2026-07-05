"""Evaluate active trade plans against fresh market data at close.

Used by the daily close scheduler job and the manual "advance trading day"
button. A plan is *triggered* when a stop / take-profit level is hit, or when
a condition tagged ``stop`` / ``take_profit`` / ``full`` is satisfied.
Entry-level touches and ``entry`` conditions are informational (an opportunity,
not a close event) so they do not mark the plan triggered.

Price-level checks are exact; indicator conditions (golden / death cross via
MA5/MA20, operating cash flow positive) are best-effort — when the data source
is unavailable the condition is reported as ``pending`` so the user can review
manually instead of being silently ignored.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class PlanDataProvider(Protocol):
    """Abstract data access so the evaluator is unit-testable."""

    async def latest_close(self, symbol: str) -> dict[str, Any] | None: ...
    async def ma_cross(self, symbol: str) -> str | None: ...
    async def cashflow_positive_recent(self, symbol: str) -> bool: ...


async def evaluate_plan(
    plan: dict[str, Any],
    *,
    data: PlanDataProvider | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one plan. Returns ``{triggered, reason, details, price, trade_date}``."""
    provider = data if data is not None else _LiveDataProvider(config or {})
    symbol = str(plan.get("symbol") or "")
    details: list[str] = []
    triggered = False
    reason = ""

    quote = await provider.latest_close(symbol)
    price = _float(quote.get("close")) if quote else None
    trade_date = quote.get("trade_date") if quote else None
    if price is None:
        return {
            "triggered": False,
            "reason": "no latest close",
            "details": ["latest_close unavailable"],
            "price": None,
            "trade_date": None,
        }

    stop_loss = _float(plan.get("stop_loss"))
    targets = plan.get("targets") or []
    entry_zone = plan.get("entry_zone") or []

    if stop_loss is not None and price <= stop_loss:
        triggered = True
        reason = f"stop_loss hit {price} <= {stop_loss}"
        details.append(reason)
    for t in targets:
        tv = _float(t)
        if tv is not None and price >= tv:
            triggered = True
            reason = f"take_profit hit {price} >= {tv}"
            details.append(reason)
            break
    if entry_zone and not triggered:
        lo = _float(entry_zone[0])
        hi = _float(entry_zone[-1])
        if lo is not None and hi is not None and lo <= price <= hi:
            details.append(f"price {price} within entry_zone [{lo}, {hi}]")

    for cond in plan.get("conditions") or []:
        kind = str(cond.get("kind") or "").lower()
        text = f"{cond.get('description') or ''} {cond.get('source') or ''}".lower()
        cond_result = await _evaluate_condition_text(text, kind, symbol, provider)
        if cond_result:
            details.append(f"condition[{kind}]: {cond_result}")
            if "satisfied" in cond_result and kind in ("stop", "take_profit", "full"):
                if kind in ("stop", "take_profit") and not reason:
                    triggered = True
                    reason = f"condition[{kind}] satisfied: {cond_result}"

    return {
        "triggered": triggered,
        "reason": reason,
        "details": details,
        "price": price,
        "trade_date": trade_date,
    }


async def _evaluate_condition_text(
    text: str, kind: str, symbol: str, provider: PlanDataProvider
) -> str:
    """Best-effort keyword detection of golden/death cross + cash-flow signals."""
    if "金叉" in text or "golden cross" in text:
        cross = await provider.ma_cross(symbol)
        if cross == "golden":
            return "satisfied: MA5 golden cross MA20"
        return "pending: golden cross not detected"
    if "死叉" in text or "death cross" in text:
        cross = await provider.ma_cross(symbol)
        if cross == "death":
            return "satisfied: MA5 death cross MA20"
        return "pending: death cross not detected"
    if "现金流" in text or "cash flow" in text:
        if await provider.cashflow_positive_recent(symbol):
            return "satisfied: operating cash flow positive (recent 2 quarters)"
        return "pending: cash flow condition not met"
    return "needs manual review"


class _LiveDataProvider:
    """Production provider: latest_close (MCP/dataflows) + daily MA + cashflow."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._daily_cache: dict[str, list[dict[str, Any]]] = {}

    async def latest_close(self, symbol: str) -> dict[str, Any] | None:
        from tradingagents.core.portfolio_prices import latest_close
        try:
            return await latest_close(symbol, self.config)
        except Exception as exc:
            logger.info("plan_evaluator latest_close failed for %s: %s", symbol, exc)
            return None

    async def ma_cross(self, symbol: str) -> str | None:
        rows = await self._daily(symbol)
        closes = [_float(r.get("close")) for r in rows]
        closes = [c for c in closes if c is not None]
        if len(closes) < 21:
            return None
        ma5_prev = sum(closes[-6:-1]) / 5
        ma5_curr = sum(closes[-5:]) / 5
        ma20_prev = sum(closes[-21:-1]) / 20
        ma20_curr = sum(closes[-20:]) / 20
        if ma5_prev <= ma20_prev and ma5_curr > ma20_curr:
            return "golden"
        if ma5_prev >= ma20_prev and ma5_curr < ma20_curr:
            return "death"
        return None

    async def cashflow_positive_recent(self, symbol: str) -> bool:
        try:
            from tradingagents.dataflows.tushare_fundamentals import _fetch_statement
            df = await asyncio.to_thread(_fetch_statement, "cashflow", symbol, "quarterly", None)
            if df is None or len(df) < 2 or "n_cashflow_act" not in df.columns:
                return False
            recent = df.head(2)["n_cashflow_act"].tolist()
            vals = [_float(v) for v in recent]
            return len(vals) >= 2 and all(v is not None and v > 0 for v in vals[:2])
        except Exception as exc:
            logger.info("plan_evaluator cashflow failed for %s: %s", symbol, exc)
            return False

    async def _daily(self, symbol: str) -> list[dict[str, Any]]:
        if symbol in self._daily_cache:
            return self._daily_cache[symbol]
        rows: list[dict[str, Any]] = []
        try:
            from datetime import date, timedelta
            from tradingagents.core.mcp_client import get_mcp_client
            end = date.today().isoformat().replace("-", "")
            start = (date.today() - timedelta(days=60)).isoformat().replace("-", "")
            client = await get_mcp_client(self.config)
            if client is not None:
                payload = await client.get_stock_daily([symbol], start, end)
                rows = _extract_daily_rows(payload, symbol)
        except Exception as exc:
            logger.info("plan_evaluator daily fetch failed for %s: %s", symbol, exc)
        self._daily_cache[symbol] = rows
        return rows


def _extract_daily_rows(payload: Any, symbol: str) -> list[dict[str, Any]]:
    """Parse MCP get_stock_daily payload into a list of {trade_date, close} rows."""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("rows")
    if not rows:
        data = payload.get("data")
        if isinstance(data, dict):
            rows = data.get(symbol) or next(iter(data.values()), [])
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict) and r.get("close") is not None:
            out.append({"trade_date": r.get("trade_date"), "close": r.get("close")})
    return out


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["evaluate_plan", "PlanDataProvider"]
