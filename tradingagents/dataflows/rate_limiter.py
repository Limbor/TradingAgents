"""In-process token-bucket rate limiters for CN data vendors.

AKShare and TuShare both throttle clients aggressively. This module
exposes two module-level buckets that all CN vendor implementations
must ``acquire()`` before issuing a request.

Parameters are read lazily from config so users can override without
restarting interpreter setup.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class TokenBucket:
    """Thread-safe token bucket.

    Args:
        rate: tokens added per second.
        capacity: maximum tokens the bucket can hold (defaults to ``rate``
            rounded up, so peak burst == 1 second worth of requests).
    """

    def __init__(self, rate: float, capacity: Optional[float] = None):
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate!r}")
        self._rate = float(rate)
        self._capacity = float(capacity if capacity is not None else max(1.0, rate))
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def update_rate(self, rate: float, capacity: Optional[float] = None) -> None:
        """Hot-swap the rate (used when config changes at runtime)."""
        with self._lock:
            self._rate = float(rate)
            self._capacity = float(capacity if capacity is not None else max(1.0, rate))
            self._tokens = min(self._tokens, self._capacity)

    def acquire(self, tokens: float = 1.0) -> None:
        """Block until ``tokens`` are available."""
        if tokens > self._capacity:
            raise ValueError(
                f"requested {tokens} tokens exceeds capacity {self._capacity}"
            )
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                sleep_for = deficit / self._rate
            # sleep outside lock so other threads can also refill
            time.sleep(sleep_for)


# Module-level instances. Default rates are conservative; real values
# are synced from config on first import of the CN vendor modules via
# _sync_from_config() below.
akshare_bucket = TokenBucket(rate=1.0)
tushare_bucket = TokenBucket(rate=3.0)

_config_synced = False


def _sync_from_config() -> None:
    """Read current config and update bucket rates. Idempotent."""
    global _config_synced
    try:
        from .config import get_config
        cfg = get_config()
        akshare_bucket.update_rate(float(cfg.get("akshare_rate_limit", 1.0)))
        tushare_bucket.update_rate(float(cfg.get("tushare_rate_limit", 3.0)))
        _config_synced = True
    except Exception:
        # Config layer may not be ready during import; leave defaults.
        pass


def acquire_akshare() -> None:
    if not _config_synced:
        _sync_from_config()
    akshare_bucket.acquire()


def acquire_tushare() -> None:
    if not _config_synced:
        _sync_from_config()
    tushare_bucket.acquire()
