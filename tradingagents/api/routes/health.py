"""Health check endpoint."""

import time

from fastapi import APIRouter, Request

from tradingagents.core.mcp_client import get_mcp_status

router = APIRouter()

_MCP_HEALTH_TTL_SECONDS = 60.0


@router.get("/health")
async def health_check(request: Request):
    """Basic health check."""
    config = getattr(request.app.state, "config", {})
    now = time.monotonic()
    checked_at = getattr(request.app.state, "mcp_status_checked_at", 0.0)
    mcp_status = getattr(request.app.state, "mcp_status", None)
    if mcp_status is None or now - checked_at >= _MCP_HEALTH_TTL_SECONDS:
        mcp_status = await get_mcp_status(config)
        request.app.state.mcp_status = mcp_status
        request.app.state.mcp_status_checked_at = now
    return {
        "status": "ok",
        "service": "tradingagents-api",
        "stockmanager_mcp": mcp_status.as_dict(),
    }
