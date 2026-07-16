"""Common helpers for AKShare-backed data flow implementations.

Provides a rate-limited ``call()`` wrapper and shared exceptions. All
AKShare vendor functions should route remote calls through
``akshare_call`` so the global token bucket is honored.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .rate_limiter import acquire_akshare

logger = logging.getLogger(__name__)


class AKShareError(RuntimeError):
    """Base error for AKShare vendor operations."""


class AKShareRateLimitError(AKShareError):
    """Raised when AKShare rejects a request due to upstream throttling."""


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
    try:
        return func(*args, **kwargs)
    except AKShareRateLimitError:
        raise  # already wrapped — pass through
    except Exception as exc:
        # AKShare vendors (East Money, Sina, etc.) return Chinese
        # error messages like "请求太频繁" that won't match English
        # keywords.  Convert **all** remote failures so the fallback
        # chain in route_to_vendor always kicks in.
        raise AKShareRateLimitError(str(exc)) from exc


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
