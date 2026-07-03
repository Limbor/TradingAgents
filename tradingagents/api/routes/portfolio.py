"""Portfolio holding endpoints."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tradingagents.core.portfolio_prices import latest_close, resolve_portfolio_symbol_async

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
    return request.app.state.db.list_holdings()


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
        request.app.state.db.upsert_holding(
            symbol=symbol,
            quantity=float(holding["quantity"]),
            avg_cost=float(holding["avg_cost"]),
            current_price=float(quote["close"]),
            notes=holding.get("notes"),
        )
        updated += 1
    return {
        "updated": updated,
        "failed": failed,
        "holdings": request.app.state.db.list_holdings(),
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
    return request.app.state.db.upsert_holding(
        symbol=resolved_body,
        quantity=body.quantity,
        avg_cost=body.avg_cost,
        current_price=current_price,
        notes=body.notes,
    )


@router.delete("/holdings/{symbol}")
async def delete_holding(request: Request, symbol: str):
    resolved = await resolve_portfolio_symbol_async(symbol)
    deleted = request.app.state.db.delete_holding(resolved)
    if not deleted:
        raise HTTPException(status_code=404, detail="Holding not found")
    return {"status": "deleted", "symbol": resolved}
