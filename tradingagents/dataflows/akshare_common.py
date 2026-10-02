"""Common helpers for AKShare-backed data flow implementations.

Provides a rate-limited ``call()`` wrapper and shared exceptions. All
AKShare vendor functions should route remote calls through
``akshare_call`` so the global token bucket is honored.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from .rate_limiter import acquire_akshare

logger = logging.getLogger(__name__)

# Hard wall-clock cap per remote call. AKShare issues bare ``requests``
# calls without timeouts, so a broken proxy/TUN route can hang a call
# forever and stall the whole pipeline. 30s is generous for any healthy
# endpoint; override via AKSHARE_CALL_TIMEOUT if needed.
_CALL_TIMEOUT = float(os.environ.get("AKSHARE_CALL_TIMEOUT", "30"))
_CALL_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="akshare-call")


class AKShareError(RuntimeError):
    """Base error for AKShare vendor operations."""


class AKShareRateLimitError(AKShareError):
    """Raised when AKShare rejects a request due to upstream throttling."""


class AKShareCallError(AKShareError):
    """Raised for upstream availability/parsing failures that may use fallback."""


def akshare_call(func: Callable[..., Any], *args, **kwargs) -> Any:
    """Invoke an ``akshare`` function under the global rate limit.

    AKShare hits public endpoints (eastmoney, sina, xueqiu, ...) directly
    and has no built-in throttling, so repeated calls can get the IP
    banned. The global ``akshare_bucket`` guarantees a minimum spacing.

    **Any** exception from the remote call is converted to
    ``AKShareRateLimitError`` so that ``route_to_vendor`` always
    tries the next fallback vendor. Genuine bugs in the call preamble
    (e.g. missing import) should not reach this wrapper; if they do,
    the error text is preserved in the exception message.
    """
    acquire_akshare()
    # A bounded executor limits abandoned upstream calls to eight worker
    # threads. Per-call daemon threads previously accumulated without bound
    # whenever a proxy/socket stayed hung after the caller timed out.
    future = _CALL_EXECUTOR.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=_CALL_TIMEOUT)
    except FutureTimeoutError as exc:
        name = getattr(func, "__name__", str(func))
        logger.warning("akshare call %s timed out after %.0fs", name, _CALL_TIMEOUT)
        future.cancel()
        raise AKShareCallError(
            f"{name} timed out after {_CALL_TIMEOUT:.0f}s (no response from upstream)"
        ) from exc
    except Exception as exc:
        if isinstance(exc, AKShareRateLimitError):
            raise exc  # already wrapped — pass through
        message = str(exc).lower()
        if any(token in message for token in (
            "请求太频繁", "访问频繁", "rate limit", "too many requests", "429",
        )):
            raise AKShareRateLimitError(str(exc)) from exc
        # Surface caller/programming errors instead of mislabelling them as an
        # upstream throttle; this makes broken adapters visible in tests/logs.
        if isinstance(exc, (TypeError, AttributeError, KeyError, AssertionError)):
            raise
        raise AKShareCallError(str(exc)) from exc


def ak_lazy_import():
    """Import akshare lazily; raise a clear error if missing."""
    try:
        import akshare as ak  # noqa: F401
        return ak
    except ImportError as exc:
        raise AKShareError(
            "akshare is not installed. Install with `pip install akshare` "
            "or `uv sync` to get project dependencies."
        ) from exc


def df_to_csv_report(df, title: str, header_lines: list[str] | None = None) -> str:
    """Render a DataFrame as a CSV report with a markdown-ish header block."""
    from datetime import datetime

    if df is None or (hasattr(df, "empty") and df.empty):
        return f"# {title}\n# No data returned.\n"

    lines = [f"# {title}"]
    lines.append(f"# Retrieved: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if header_lines:
        lines.extend(f"# {line}" for line in header_lines)
    lines.append(f"# Rows: {len(df)}")
    lines.append("")

    return "\n".join(lines) + "\n" + df.to_csv(index=False)
