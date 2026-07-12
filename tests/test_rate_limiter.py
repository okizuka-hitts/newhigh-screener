"""RateLimiter のテスト(最小間隔の順守・実測レート・異常入力)。"""

import pytest

from helpers import FakeClock

from screener import config
from screener.api.rate_limiter import RateLimiter


def _make(rate):
    clock = FakeClock()
    return RateLimiter(rate, time_func=clock.time, sleep_func=clock.sleep), clock


def test_first_acquire_does_not_sleep():
    limiter, clock = _make(60.0)
    limiter.acquire()
    assert clock.sleeps == []


def test_enforces_min_interval_between_requests():
    rate = 60.0  # 毎分60 → 最小間隔1秒
    limiter, clock = _make(rate)
    times = [limiter.acquire() for _ in range(5)]
    diffs = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(d >= limiter.min_interval - 1e-9 for d in diffs)
    assert len(clock.sleeps) == 4  # 2回目以降は待機


def test_measured_rate_never_exceeds_effective_cap():
    rate = config.effective_rate_limit_per_min()
    limiter, _ = _make(rate)
    for _ in range(30):
        limiter.acquire()
    measured = limiter.measured_rate_per_min()
    assert measured <= rate + 1e-6
    assert measured <= config.effective_rate_limit_per_min() + 1e-6


def test_measured_rate_zero_before_two_requests():
    limiter, _ = _make(60.0)
    assert limiter.measured_rate_per_min() == 0.0
    limiter.acquire()
    assert limiter.measured_rate_per_min() == 0.0


def test_invalid_rate_raises():
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        RateLimiter(-5)


def test_window_prunes_old_timestamps():
    clock = FakeClock()
    limiter = RateLimiter(
        600.0, time_func=clock.time, sleep_func=clock.sleep, window_seconds=1.0
    )
    limiter.acquire()
    clock.now += 10.0  # ウィンドウ超過
    limiter.acquire()
    # 古いタイムスタンプは掃き出され、実測は2点未満相当で0。
    assert limiter.measured_rate_per_min() == 0.0
