"""レートリミッタ(NFR-1)。

J-Quantsライトプランへのアクセス頻度を実効上限(上限×安全係数)以内に抑える。
時刻・スリープ関数を注入可能にし、ユニットテストで決定的に検証できるようにする。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """毎分あたりのリクエスト数を上限以内に制御する最小間隔ベースのリミッタ。

    連続する `acquire()` の間隔が `60 / rate_per_min` 秒未満なら、その差分だけ
    `sleep_func` で待機する。直近ウィンドウ内の実測レートを算出できる。
    """

    def __init__(
        self,
        rate_per_min: float,
        *,
        time_func: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], None] = time.sleep,
        window_seconds: float = 60.0,
    ) -> None:
        if rate_per_min <= 0:
            raise ValueError("rate_per_min は正の値である必要があります")
        self.rate_per_min = rate_per_min
        self.min_interval = 60.0 / rate_per_min
        self._time = time_func
        self._sleep = sleep_func
        self._window = window_seconds
        self._last: float | None = None
        self._timestamps: deque[float] = deque()

    def acquire(self) -> float:
        """1リクエスト分のスロットを確保する。必要なら待機し、確保時刻を返す。"""
        now = self._time()
        if self._last is not None:
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                self._sleep(wait)
                now = self._time()
        self._last = now
        self._timestamps.append(now)
        self._prune(now)
        return now

    def _prune(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] > self._window:
            self._timestamps.popleft()

    def measured_rate_per_min(self) -> float:
        """直近ウィンドウ内の実測リクエストレート(毎分)を返す。"""
        now = self._time()
        self._prune(now)
        if len(self._timestamps) < 2:
            return 0.0
        span = self._timestamps[-1] - self._timestamps[0]
        if span <= 0:
            return 0.0
        # ウィンドウ内の間隔数 / 経過時間 でレートを推定する。
        return (len(self._timestamps) - 1) / span * 60.0
