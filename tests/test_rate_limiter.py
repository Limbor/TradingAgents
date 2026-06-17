"""Unit tests for tradingagents.dataflows.rate_limiter.

Uses a monkeypatched monotonic clock so acquire() never blocks on real
wall-clock sleeps.
"""
from __future__ import annotations

import pytest

from tradingagents.dataflows import rate_limiter
from tradingagents.dataflows.rate_limiter import TokenBucket


class FakeClock:
    """Deterministic monotonic clock used by TokenBucket under test."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(rate_limiter.time, "monotonic", clock.now)
    # Turn blocking sleep into a no-op AND advance the fake clock so the
    # next loop iteration sees refilled tokens.
    def fake_sleep(sec: float) -> None:
        clock.advance(sec)
    monkeypatch.setattr(rate_limiter.time, "sleep", fake_sleep)
    return clock


@pytest.mark.unit
class TestTokenBucket:
    def test_rejects_non_positive_rate(self):
        with pytest.raises(ValueError):
            TokenBucket(rate=0)
        with pytest.raises(ValueError):
            TokenBucket(rate=-1.5)

    def test_initial_burst_capacity(self, fake_clock):
        bucket = TokenBucket(rate=3.0)  # capacity defaults to 3
        # 3 back-to-back acquires must not sleep (burst).
        for _ in range(3):
            bucket.acquire()
        assert fake_clock.t == pytest.approx(1000.0)

    def test_throttles_after_burst(self, fake_clock):
        bucket = TokenBucket(rate=1.0, capacity=1.0)
        bucket.acquire()  # consume initial token
        t0 = fake_clock.t
        bucket.acquire()  # must sleep ~1s for refill
        assert fake_clock.t - t0 == pytest.approx(1.0, rel=1e-3)

    def test_refill_is_linear_in_rate(self, fake_clock):
        bucket = TokenBucket(rate=2.0, capacity=2.0)
        bucket.acquire()
        bucket.acquire()  # bucket now empty
        t0 = fake_clock.t
        bucket.acquire()  # should wait ~0.5s
        assert fake_clock.t - t0 == pytest.approx(0.5, rel=1e-3)

    def test_update_rate_hotswap(self, fake_clock):
        bucket = TokenBucket(rate=1.0, capacity=1.0)
        bucket.update_rate(rate=5.0, capacity=5.0)
        # After hotswap capacity=5 but initial tokens were 1 and got clipped
        # by min(_tokens, _capacity); so we can only burst 1 before
        # accruing more. Advance time to fill the bucket.
        fake_clock.advance(1.0)  # +5 tokens, capped at 5
        t0 = fake_clock.t
        for _ in range(5):
            bucket.acquire()
        # All 5 served from the refilled pool without further sleep.
        assert fake_clock.t == pytest.approx(t0)

    def test_acquire_more_than_capacity_raises(self, fake_clock):
        bucket = TokenBucket(rate=2.0, capacity=2.0)
        with pytest.raises(ValueError):
            bucket.acquire(tokens=10.0)


@pytest.mark.unit
class TestModuleBuckets:
    def test_module_level_buckets_exist(self):
        assert isinstance(rate_limiter.akshare_bucket, TokenBucket)
        assert isinstance(rate_limiter.tushare_bucket, TokenBucket)

    def test_sync_from_config_updates_rates(self, monkeypatch):
        fake_cfg = {"akshare_rate_limit": 4.0, "tushare_rate_limit": 7.0}
        monkeypatch.setattr(
            "tradingagents.dataflows.config.get_config",
            lambda: fake_cfg,
        )
        rate_limiter._config_synced = False
        rate_limiter._sync_from_config()
        assert rate_limiter.akshare_bucket._rate == pytest.approx(4.0)
        assert rate_limiter.tushare_bucket._rate == pytest.approx(7.0)

    def test_acquire_helpers_are_non_blocking_after_refill(self, fake_clock):
        # Fresh bucket -> first acquire should not sleep.
        rate_limiter.akshare_bucket = TokenBucket(rate=2.0, capacity=2.0)
        t0 = fake_clock.t
        rate_limiter._config_synced = True  # skip auto-sync
        rate_limiter.acquire_akshare()
        assert fake_clock.t == pytest.approx(t0)
