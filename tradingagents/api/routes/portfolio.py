"""Portfolio holding endpoints."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tradingagents.core.portfolio_prices import latest_close, resolve_portfolio_name, resolve_portfolio_symbol_async

router = APIRouter()


class HoldingInput(BaseModel):
    symbol: str
    quantity: float = Field(ge=0)
    avg_cost: float = Field(ge=0)
    current_price: float | None = Field(default=None, ge=0)
    notes: str | None = None


class RefreshPricesResponse(BaseModel):
    updated: int
    failed: list[dict]
    holdings: list[dict]


@router.get("/holdings")
async def list_holdings(request: Request):
    db = request.app.state.db
    return _with_holding_names(db, db.list_holdings())


@router.post("/portfolio/refresh-prices", response_model=RefreshPricesResponse)
@router.post("/holdings/refresh-prices", response_model=RefreshPricesResponse)
async def refresh_holding_prices(request: Request):
    holdings = request.app.state.db.list_holdings()
    updated = 0
    failed = []
    for holding in holdings:
        symbol = holding["symbol"]
        quote = await latest_close(symbol, request.app.state.config)
        if quote is None:
            failed.append({"symbol": symbol, "reason": "latest close unavailable"})
            continue
        # Update only the price column — upsert_holding would rewrite
        # quantity/avg_cost/notes and race with concurrent user edits.
        request.app.state.db.update_holding_price(
            symbol=symbol,
            current_price=float(quote["close"]),
        )
        updated += 1
    return {
        "updated": updated,
        "failed": failed,
        "holdings": _with_holding_names(request.app.state.db, request.app.state.db.list_holdings()),
    }


@router.post("/portfolio/advance-trading-day")
async def advance_trading_day(request: Request):
    """Refresh closing prices + evaluate active plans + return the trading-day context.

    The unified "进入下一交易日" button: combines the old "刷新收盘价" (price
    refresh) with plan monitoring, and returns the asof/session so the UI can
    show which trading day the system is now anchored to.
    """
    db = request.app.state.db
    config = request.app.state.config
    holdings = db.list_holdings()
    updated = 0
    failed: list[dict] = []
    for holding in holdings:
        symbol = holding["symbol"]
        quote = await latest_close(symbol, config)
        if quote is None:
            failed.append({"symbol": symbol, "reason": "latest close unavailable"})
            continue
        db.update_holding_price(symbol=symbol, current_price=float(quote["close"]))
        updated += 1

    from tradingagents.core.plan_monitor import evaluate_active_plans
    from tradingagents.core.trading_time import get_temporal_context

    alerts = await evaluate_active_plans(db, config)
    ctx = get_temporal_context(config, market="cn_a").to_dict()
    return {
        "refreshed_prices": {
            "updated": updated,
            "failed": failed,
            "holdings": _with_holding_names(db, db.list_holdings()),
        },
        "plan_alerts": alerts,
        "temporal_context": ctx,
    }


@router.put("/holdings/{symbol}")
async def upsert_holding(request: Request, symbol: str, body: HoldingInput):
    resolved_path = await resolve_portfolio_symbol_async(symbol)
    resolved_body = await resolve_portfolio_symbol_async(body.symbol)
    if resolved_path != resolved_body:
        raise HTTPException(status_code=400, detail="Path symbol must match body symbol")
    current_price = body.current_price
    if current_price is None:
        quote = await latest_close(resolved_body, request.app.state.config)
        if quote is not None:
            current_price = quote["close"]
    holding = request.app.state.db.upsert_holding(
        symbol=resolved_body,
        quantity=body.quantity,
        avg_cost=body.avg_cost,
        current_price=current_price,
        notes=body.notes,
    )
    return _with_holding_name(request.app.state.db, holding)


@router.delete("/holdings/{symbol}")
async def delete_holding(request: Request, symbol: str):
    resolved = await resolve_portfolio_symbol_async(symbol)
    deleted = request.app.state.db.delete_holding(resolved)
    if not deleted:
        raise HTTPException(status_code=404, detail="Holding not found")
    return {"status": "deleted", "symbol": resolved}


def _with_holding_names(db, holdings: list[dict]) -> list[dict]:
    return [_with_holding_name(db, item) for item in holdings]


def _with_holding_name(db, holding: dict) -> dict:
    item = dict(holding)
    symbol = str(item.get("symbol") or "")
    item["name"] = resolve_portfolio_name(symbol)
    item["latest_analysis"] = _latest_analysis_for_symbol(db, symbol)
    return item


def _latest_analysis_for_symbol(db, symbol: str) -> dict | None:
    artifacts = db.list_artifacts(
        limit=1,
        artifact_type="stock_report",
        subject_type="ticker",
        subject_id=symbol,
    )
    if artifacts:
        artifact = artifacts[0]
        payload = artifact.get("payload") or {}
        return {
            "artifact_id": artifact.get("id"),
            "run_id": artifact.get("run_id"),
            "date": _date_part(artifact.get("created_at")),
            "created_at": artifact.get("created_at"),
            "rating": payload.get("rating") or artifact.get("subtitle"),
            "summary": _analysis_summary(
                artifact.get("summary"),
                artifact.get("content_markdown"),
                payload.get("rating") or artifact.get("subtitle"),
            ),
            "title": artifact.get("title"),
        }

    reports = db.list_reports(limit=1, ticker=symbol)
    if not reports:
        return None
    report = reports[0]
    return {
        "artifact_id": None,
        "run_id": report.get("run_id"),
        "date": _date_part(report.get("created_at")),
        "created_at": report.get("created_at"),
        "rating": report.get("rating"),
        "summary": _analysis_summary(report.get("rating"), report.get("content")),
        "title": f"{report.get('ticker_name') or symbol} 投资报告",
    }


def _analysis_summary(summary: str | None, content: str | None, rating: str | None = None) -> str | None:
    if summary and summary != rating:
        return _truncate(summary)
    if rating:
        return _truncate(rating)
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("|") or set(line) <= {"-", ":"}:
            continue
        return _truncate(line)
    return None


def _truncate(value: str, limit: int = 140) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def _date_part(value: str | None) -> str | None:
    return value[:10] if value else None
