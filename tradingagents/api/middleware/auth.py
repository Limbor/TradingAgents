"""Optional token-based auth middleware.

When ``api_auth_token`` is set in config (env ``TRADINGAGENTS_API_AUTH_TOKEN``),
REST requests must carry ``Authorization: Bearer <token>`` (or ``?token=``).
WebSocket endpoints read the token from the ``?token=`` query parameter since
browsers cannot set headers on the WS handshake.

When the token is empty (the default), auth is disabled — preserving the
local-desktop single-user experience. Set the env var when exposing the API
beyond localhost (e.g. LAN or cloud deployment).
"""

from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Paths exempt from auth: the health probe (used by Docker/uptime monitors) and
# the OpenAPI docs. Everything under /api/v1 and /ws requires the token when set.
EXEMPT_PATH_PREFIXES = ("/api/v1/health", "/docs", "/openapi.json", "/redoc")


def auth_token_configured(config: dict) -> str:
    """Return the configured auth token (empty string when auth is disabled)."""
    return str((config or {}).get("api_auth_token") or "").strip()


def request_has_valid_token(request: Request, expected: str) -> bool:
    """True if the request carries the expected bearer token."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        supplied = auth.split(" ", 1)[1].strip()
        return hmac.compare_digest(supplied, expected)
    # Allow ?token= as a fallback (useful for SSE/WS-style clients).
    supplied = request.query_params.get("token") or ""
    return bool(supplied) and hmac.compare_digest(supplied, expected)


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce a static bearer token on REST routes when configured.

    The token is read from ``app.state.config["api_auth_token"]`` at request
    time (populated by the lifespan), so it can be set via env without
    rebuilding the app. When the key is absent or empty, auth is disabled.
    """

    async def dispatch(self, request: Request, call_next):
        config = getattr(request.app.state, "config", {}) or {}
        token = auth_token_configured(config)
        if not token:
            return await call_next(request)
        path = request.url.path
        if path.startswith(EXEMPT_PATH_PREFIXES):
            return await call_next(request)
        # Only protect API + WS-related HTTP upgrades; static + docs are open.
        if not (path.startswith("/api/") or path.startswith("/ws")):
            return await call_next(request)
        if not request_has_valid_token(request, token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid auth token"},
            )
        return await call_next(request)


def verify_ws_token(query_params, config: dict) -> bool:
    """Verify the ``?token=`` query param for WebSocket handshakes.

    Returns True when auth is disabled (no token configured) or the token matches.
    """
    expected = auth_token_configured(config)
    if not expected:
        return True
    supplied = (query_params.get("token") if query_params else "") or ""
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def allowed_origins(config: dict | None) -> list[str]:
    """Return the explicit browser/Tauri origin allow-list."""
    raw = (config or {}).get("api_allowed_origins") or ""
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = []
    return [str(value).strip().rstrip("/") for value in values if str(value).strip()]


def verify_ws_origin(headers, config: dict | None) -> bool:
    """Reject browser WebSockets from origins outside the configured allow-list.

    Non-browser clients commonly omit Origin; token authentication remains the
    authority for those clients and keeps CLI/integration consumers working.
    """
    origin = ((headers.get("origin") if headers else "") or "").strip().rstrip("/")
    return not origin or origin in allowed_origins(config)
