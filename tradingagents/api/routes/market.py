"""Market overview endpoint — reads the latest market_overview artifact.

Pure SQLite read: never triggers AKShare calls. Refresh is done by the
frontend via ``POST /api/v1/runs {skill_id: "market_overview"}``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from tradingagents.core.industry_taxonomy import (
    canonicalize_investor_theme,
    industry_selection_metadata,
    is_investable_industry_concept,
)
from tradingagents.core.mcp_client import get_mcp_client
from tradingagents.core.trading_time import get_temporal_context

router = APIRouter()
logger = logging.getLogger(__name__)


class TradeReviewRequest(BaseModel):
    symbol: str = Field(min_length=1)
    trade_date: str | None = None
    lookback_days: int = Field(default=120, ge=20, le=250)
    adj_type: str = "qfq"
    plan: dict[str, Any] = Field(default_factory=dict)
    recommendation_context: dict[str, Any] = Field(default_factory=dict)


@router.get("/market/overview")
async def get_market_overview(request: Request) -> dict[str, Any]:
    """Latest market overview artifact plus staleness vs. current trading day."""
    rows = request.app.state.db.list_artifacts(
        limit=1,
        artifact_type="market_overview",
        subject_id="cn_a",
    )
    ctx = get_temporal_context(request.app.state.config, market="cn_a")
    if not rows:
        return {
            "available": False,
            "artifact": None,
            "is_stale": False,
            "current_asof_date": ctx.market_asof_date,
        }
    artifact = dict(rows[0])
    payload = dict(artifact.get("payload") or {})
    # Backfill the compatibility metadata for snapshots created before the
    # Market/MCP taxonomy bridge shipped. This is response-only; the immutable
    # stored artifact remains untouched and no data-source refresh is needed.
    stances: list[dict[str, Any]] = []
    concept_by_name: dict[str, dict[str, Any]] = {}
    for raw in payload.get("industry_stances") or []:
        row = dict(raw)
        if row.get("board_type") == "concept":
            source_name = str(row.get("source_name") or row.get("industry") or "").strip()
            if not is_investable_industry_concept(source_name):
                continue
            display_name = canonicalize_investor_theme(source_name)
            row["industry"] = display_name
            row["source_name"] = source_name
            if display_name in concept_by_name:
                existing = concept_by_name[display_name]
                existing["source_names"] = list(dict.fromkeys([
                    *(existing.get("source_names") or [existing.get("source_name")]),
                    source_name,
                ]))
                continue
            row["source_names"] = list(dict.fromkeys([
                *(row.get("source_names") or []),
                source_name,
            ]))
            concept_by_name[display_name] = row
        if not isinstance(row.get("selection_industries"), list) or not row.get("selection_mode"):
            row.update(
                industry_selection_metadata(
                    str(row.get("industry") or ""),
                    str(row.get("board_type") or ""),
                    taxonomy=str(row.get("taxonomy") or "") or None,
                    industry_code=str(row.get("industry_code") or "") or None,
                    industry_level=str(row.get("industry_level") or "") or None,
                )
            )
        if row.get("board_type") == "concept" and not row.get("selection_concept"):
            row["selection_concept"] = str(
                row.get("source_name") or row.get("industry") or ""
            ).strip()
        stances.append(row)
    if "industry_stances" in payload:
        payload["industry_stances"] = stances
        artifact["payload"] = payload
    return {
        "available": True,
        "artifact": artifact,
        "is_stale": payload.get("market_asof_date") != ctx.market_asof_date,
        "current_asof_date": ctx.market_asof_date,
    }


@router.post("/market/trade-review")
async def get_trade_review(request: Request, body: TradeReviewRequest) -> dict[str, Any]:
    """Lazy candidate chart plus an MCP-authoritative pre-trade review.

    The local fallback intentionally provides chart data only.  It never
    recreates StockManager's gate, so a data-service outage cannot accidentally
    turn a degraded response into an executable recommendation.
    """
    ctx = get_temporal_context(request.app.state.config, market="cn_a")
    trade_date = body.trade_date or ctx.market_asof_date
    mcp = await get_mcp_client(request.app.state.config)
    if mcp is not None:
        if "get_trade_review_snapshot" in mcp.status.tools:
            payload = None
            for attempt in range(2):
                payload = await mcp.get_trade_review_snapshot(
                    body.symbol,
                    trade_date,
                    lookback_days=body.lookback_days,
                    adj_type=body.adj_type,
                    plan=body.plan,
                )
                if payload is not None or attempt > 0:
                    break
                logger.info(
                    "Retrying trade-review MCP call after a transient session failure: %s",
                    body.symbol,
                )
            if isinstance(payload, dict) and payload.get("status") in {"success", "partial"}:
                response = {
                    **payload,
                    "degraded": False,
                    "gate_authority": "stockmanager_mcp",
                }
                response = await _attach_chip_profile_from_mcp(
                    mcp,
                    response,
                    symbol=body.symbol,
                    trade_date=trade_date,
                    lookback_days=body.lookback_days,
                    adj_type=body.adj_type,
                )
                return _attach_recommendation_reliability(response, body.recommendation_context)

        # Compatibility with an older StockManager service: its generic daily
        # tool can still supply historical candles, but it has no authority to
        # produce an execution gate.
        try:
            start_date = (
                date.fromisoformat(trade_date) - timedelta(days=body.lookback_days * 2)
            ).isoformat()
            daily = await mcp.get_stock_daily(
                [body.symbol], start_date, trade_date, adj_type=body.adj_type
            )
            response = _mcp_daily_chart_fallback(
                daily, body.symbol, trade_date, body.lookback_days, body.plan
            )
            if response["candles"]:
                return _attach_recommendation_reliability(response, body.recommendation_context)
        except Exception as exc:
            logger.info("MCP generic daily fallback failed for %s: %s", body.symbol, exc)

    response = await asyncio.to_thread(
        _local_chart_fallback,
        body.symbol,
        trade_date,
        body.lookback_days,
        body.plan,
    )
    return _attach_recommendation_reliability(response, body.recommendation_context)


def _attach_recommendation_reliability(
    payload: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    """Fuse evidence quality separately from the recommendation's direction/score."""
    gate = payload.get("pretrade_gate") or {}
    technical = max(0.0, min(100.0, float(gate.get("reliability_score") or 0)))
    missing = len(context.get("data_coverage_warnings") or [])
    coverage = max(0.0, 100.0 - missing * 20.0)
    final_decision = str(context.get("final_decision") or "").upper()
    quant_decision = str(context.get("quant_decision") or "").upper()
    llm_view = str(context.get("llm_view") or "").lower()
    if final_decision == "BUY" and quant_decision == "BUY" and llm_view in {
        "support", "supported", "bullish", "buy", "strong_buy",
    }:
        agreement = 95.0
    elif final_decision and quant_decision and final_decision == quant_decision:
        agreement = 80.0
    elif final_decision in {"WATCHLIST", "MONITOR", "HOLD_REVIEW"}:
        agreement = 60.0
    elif final_decision and quant_decision:
        agreement = 35.0
    else:
        agreement = 45.0
    requested = str(context.get("requested_trade_date") or "")
    effective = str(payload.get("effective_trade_date") or payload.get("as_of_date") or "")
    freshness = 100.0 if not requested or requested == effective else 75.0
    if final_decision and final_decision != "BUY":
        eligibility_status = "reject" if final_decision in {"SKIP", "SELL", "AVOID"} else "wait"
        if gate.get("status") == "actionable" or eligibility_status == "reject":
            gate["status"] = eligibility_status
        detail = f"最终推荐为 {final_decision}，未达到 BUY 执行资格"
        checks = gate.setdefault("checks", [])
        if not any(item.get("code") == "recommendation_eligibility" for item in checks):
            checks.append({
                "code": "recommendation_eligibility",
                "passed": False,
                "severity": "hard" if eligibility_status == "reject" else "soft",
                "detail": detail,
            })
        reasons = gate.setdefault("reasons", [])
        if detail not in reasons:
            reasons.append(detail)
    score = round(technical * 0.45 + coverage * 0.25 + agreement * 0.20 + freshness * 0.10)
    if payload.get("degraded"):
        score = min(score, 35)
    level = "high" if score >= 80 else "medium" if score >= 60 else "low"
    payload["recommendation_reliability"] = {
        "score": score,
        "level": level,
        "components": {
            "technical_gate": round(technical),
            "data_coverage": round(coverage),
            "model_agreement": round(agreement),
            "freshness": round(freshness),
        },
        "note": "可靠性衡量证据完整度与一致性，不代表预期收益率。",
    }
    return payload


async def _attach_chip_profile_from_mcp(
    mcp: Any,
    response: dict[str, Any],
    *,
    symbol: str,
    trade_date: str,
    lookback_days: int,
    adj_type: str,
) -> dict[str, Any]:
    """Attach optional MCP chip evidence without changing its execution gate."""
    from tradingagents.core.chip_distribution import (
        compute_chip_profile,
        unavailable_chip_profile,
    )

    if isinstance(response.get("chip_profile"), dict):
        return response
    if "get_chip_distribution_snapshot" in getattr(mcp.status, "tools", []):
        try:
            payload = await mcp.get_chip_distribution_snapshot(
                symbol,
                trade_date,
                lookback_days=lookback_days,
                adj_type=adj_type,
            )
            if isinstance(payload, dict) and payload.get("status") in {"success", "available", "partial"}:
                profile = payload.get("chip_profile") or payload
                if isinstance(profile, dict):
                    response["chip_profile"] = profile
                    return response
        except Exception as exc:
            logger.info("MCP chip distribution failed for %s: %s", symbol, exc)

    # Newer generic daily responses may already include turnover_rate.  It is
    # safe to estimate locally from those point-in-time rows; volume is never
    # used as an undocumented substitute for turnover.
    import pandas as pd

    candles = response.get("candles") or []
    profile = compute_chip_profile(pd.DataFrame(candles)) if candles else None
    response["chip_profile"] = profile or unavailable_chip_profile(
        "MCP 尚未提供筹码分布，当前 K 线也缺少换手率"
    )
    return response


def _local_chart_fallback(
    symbol: str,
    trade_date: str,
    lookback_days: int,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Try both local A-share vendors; execution remains unavailable."""
    from tradingagents.dataflows.akshare_stock import load_ohlcv_cn
    from tradingagents.dataflows.tushare_stock import load_ohlcv_ts

    errors: list[str] = []
    for source, loader in (("akshare", load_ohlcv_cn), ("tushare", load_ohlcv_ts)):
        try:
            frame = loader(symbol, trade_date).copy()
            if not frame.empty:
                return _frame_chart_fallback(
                    frame,
                    symbol=symbol,
                    trade_date=trade_date,
                    lookback_days=lookback_days,
                    plan=plan,
                    source=f"tradingagents_{source}_fallback",
                    warnings=[
                        f"StockManager MCP 门控不可用：K 线来自 {source}，仅供人工查看。"
                    ],
                )
        except Exception as exc:
            logger.info("%s chart fallback failed for %s: %s", source, symbol, exc)
            errors.append(f"{source}: {type(exc).__name__}")

    return _frame_chart_fallback(
        None,
        symbol=symbol,
        trade_date=trade_date,
        lookback_days=lookback_days,
        plan=plan,
        source="tradingagents_local_fallback",
        warnings=[
            "StockManager MCP 不可用，且本地 AKShare/TuShare 均未取得历史 K 线。",
            *errors,
        ],
    )


def _mcp_daily_chart_fallback(
    payload: Any,
    symbol: str,
    trade_date: str,
    lookback_days: int,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Convert the legacy MCP get_stock_daily response to the chart contract."""
    import pandas as pd

    rows = payload.get("rows") if isinstance(payload, dict) else None
    if isinstance(rows, dict):
        values = rows.get(symbol) or rows.get(symbol.strip().upper())
        if not isinstance(values, list):
            values = next((item for item in rows.values() if isinstance(item, list)), [])
    else:
        values = []
    frame = pd.DataFrame(values)
    if not frame.empty:
        date_col = next((col for col in ("trade_date", "date", "index", "Date") if col in frame), None)
        rename = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        frame = frame.rename(columns=rename)
        if date_col:
            frame["Date"] = pd.to_datetime(frame[date_col])
        else:
            frame = pd.DataFrame()
    return _frame_chart_fallback(
        frame,
        symbol=symbol,
        trade_date=trade_date,
        lookback_days=lookback_days,
        plan=plan,
        source="stockmanager_legacy_daily",
        warnings=["StockManager 尚未加载交易复核工具：已用通用日线兼容看图，不能判定可执行。"],
    )


def _frame_chart_fallback(
    frame: Any,
    *,
    symbol: str,
    trade_date: str,
    lookback_days: int,
    plan: dict[str, Any],
    source: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Build a chart-only response from canonical Date/OHLCV columns."""
    import pandas as pd

    candles: list[dict[str, Any]] = []
    if frame is not None and not frame.empty:
        frame = frame.copy()
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        cutoff = pd.Timestamp(trade_date)
        frame = frame[frame["Date"].notna() & (frame["Date"] <= cutoff)].sort_values("Date").tail(lookback_days)
        close = frame["Close"].astype(float)
        volume = frame["Volume"].astype(float)
        for window in (5, 10, 20, 60):
            frame[f"MA{window}"] = close.rolling(window).mean()
        frame["VolumeMA5"] = volume.rolling(5).mean()
        for _, row in frame.iterrows():
            item: dict[str, Any] = {"trade_date": row["Date"].strftime("%Y-%m-%d")}
            for source_col, target in (
                ("Open", "open"), ("High", "high"), ("Low", "low"),
                ("Close", "close"), ("Volume", "volume"),
                ("Turnover", "turnover_rate"), ("turnover_rate", "turnover_rate"),
                ("MA5", "ma5"), ("MA10", "ma10"), ("MA20", "ma20"),
                ("MA60", "ma60"), ("VolumeMA5", "volume_ma5"),
            ):
                value = row.get(source_col)
                if target in item and item[target] is not None:
                    continue
                item[target] = round(float(value), 4) if value is not None and value == value else None
            candles.append(item)

    from tradingagents.core.chip_distribution import compute_chip_profile

    chip_profile = compute_chip_profile(frame)

    return {
        "status": "partial",
        "request_id": "local-fallback",
        "as_of_date": trade_date,
        "effective_trade_date": candles[-1]["trade_date"] if candles else trade_date,
        "source": source,
        "method": "chart_only_fallback_v1",
        "warnings": warnings,
        "ts_code": symbol,
        "adj_type": "qfq",
        "candles": candles,
        "chip_profile": chip_profile,
        "metrics": {},
        "tradability": {"is_tradable": None, "reason": "mcp_unavailable"},
        "plan": plan,
        "pretrade_gate": {
            "status": "wait",
            "reliability_score": 20 if candles else 0,
            "reasons": ["交易前门控服务不可用，禁止直接执行"],
            "checks": [{
                "code": "mcp_gate_available",
                "passed": False,
                "severity": "hard",
                "detail": "StockManager MCP 未连接或未声明 get_trade_review_snapshot",
            }],
        },
        "data_coverage": {
            "requested_days": lookback_days,
            "returned_days": len(candles),
            "point_in_time_cutoff": trade_date,
        },
        "degraded": True,
        "gate_authority": "none",
    }
