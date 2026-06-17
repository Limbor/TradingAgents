"""Common helpers for TuShare-backed data flow implementations.

Provides a lazily-initialized ``pro_api`` singleton and a rate-limited
call wrapper. TuShare uses a token obtained via their website; we read
``TUSHARE_TOKEN`` env var by default, with override via
``config['tushare_token']``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Optional

from .config import get_config
from .rate_limiter import acquire_tushare

logger = logging.getLogger(__name__)


class TuShareError(RuntimeError):
    """Base error for TuShare vendor operations."""


class TuShareAuthError(TuShareError):
    """Raised when the TuShare token is missing or invalid."""


class TuShareRateLimitError(TuShareError):
    """Raised when TuShare rejects a request due to credit / rate limits."""


_pro_api = None
_pro_api_lock = threading.Lock()


def _resolve_token() -> str:
    token = get_config().get("tushare_token") or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise TuShareAuthError(
            "TuShare token missing. Set TUSHARE_TOKEN env var or "
            "config['tushare_token']. Obtain one from https://tushare.pro/."
        )
    return str(token).strip()


def get_pro_api():
    """Return a cached TuShare ``pro_api`` client."""
    global _pro_api
    if _pro_api is not None:
        return _pro_api
    with _pro_api_lock:
        if _pro_api is not None:
            return _pro_api
        try:
            import tushare as ts  # type: ignore
        except ImportError as exc:
            raise TuShareError(
                "tushare is not installed. Install with `pip install tushare` "
                "or `uv sync`."
            ) from exc
        token = _resolve_token()
        ts.set_token(token)
        _pro_api = ts.pro_api()
        return _pro_api


def tushare_call(func: Callable[..., Any], *args, **kwargs) -> Any:
    """Invoke a TuShare endpoint under the global token bucket.

    **Any** exception from the remote call is converted to
    ``TuShareRateLimitError`` so that ``route_to_vendor`` always
    tries the next fallback vendor.
    """
    acquire_tushare()
    try:
        return func(*args, **kwargs)
    except TuShareRateLimitError:
        raise
    except Exception as exc:
        raise TuShareRateLimitError(str(exc)) from exc
