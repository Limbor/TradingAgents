"""Trading temporal context endpoint."""

from fastapi import APIRouter, Request

from tradingagents.core.trading_time import get_temporal_context

router = APIRouter()


@router.get("/trading-time")
async def trading_time(request: Request, market: str = "cn_a", requested_date: str | None = None):
    context = get_temporal_context(
        request.app.state.config,
        market=market,
        requested_date=requested_date,
    )
    return context.to_dict()
