"""進行状況表示(NS-18)。

fetch の営業日ループ(by-date)の進捗を、TTY環境では %・プログレスバー・残り推定時間
(ETA)を stderr に更新表示(`\\r` で行を上書き)し、非TTY環境(CI・Routine)では行単位の
ログ出力にフォールバックして進行情報を失わないようにする。

依存は追加しない(stdlibのみ)。時刻・出力先・TTY判定を注入可能にし、ユニットテストで
決定的に検証できる。ETAは経過時間×残数/完了数の線形推定(追加APIリクエストは発生しない)。
"""

from __future__ import annotations

import logging
import sys
import time as _time
from collections.abc import Callable
from typing import TextIO

logger = logging.getLogger("screener.fetch")


class ProgressReporter:
    """進捗の集計と描画を担う。`advance()` を1ステップごとに呼ぶ。"""

    def __init__(
        self,
        total: int,
        *,
        label: str = "",
        stream: TextIO | None = None,
        time_func: Callable[[], float] = _time.monotonic,
        isatty: bool | None = None,
        bar_width: int = 20,
    ) -> None:
        self.total = max(0, int(total))
        self.label = label
        self._stream = stream if stream is not None else sys.stderr
        self._time = time_func
        self._bar_width = max(1, bar_width)
        if isatty is None:
            isatty = bool(getattr(self._stream, "isatty", lambda: False)())
        self._isatty = bool(isatty)
        self._done = 0
        self._start = self._time()

    def advance(self, step: int = 1) -> None:
        """進捗を step 分進めて再描画する。"""
        self._done = min(self.total, self._done + step) if self.total else self._done + step
        self._render()

    def _eta_seconds(self) -> float | None:
        """残り推定秒。1件も完了していなければ None。"""
        if self._done <= 0:
            return None
        elapsed = self._time() - self._start
        remaining = max(0, self.total - self._done)
        return (elapsed / self._done) * remaining

    def _format(self) -> str:
        fraction = (self._done / self.total) if self.total else 1.0
        fraction = min(1.0, max(0.0, fraction))
        percent = fraction * 100
        filled = int(round(self._bar_width * fraction))
        bar = "#" * filled + "-" * (self._bar_width - filled)
        eta = self._eta_seconds()
        eta_str = f"残り ~{eta:.0f}s" if eta is not None else "残り ~--s"
        head = f"{self.label} " if self.label else ""
        return f"{head}[{bar}] {percent:3.0f}% ({self._done}/{self.total}) {eta_str}"

    def _render(self) -> None:
        text = self._format()
        if self._isatty:
            self._stream.write("\r" + text)
            self._stream.flush()
        else:
            logger.info("進捗 %s", text)

    def finish(self) -> None:
        """完了表示。TTYでは行を確定(改行)し、非TTYでは完了ログを1行出す。"""
        if self.total:
            self._done = self.total
        text = self._format()
        if self._isatty:
            self._stream.write("\r" + text + "\n")
            self._stream.flush()
        else:
            logger.info("進捗完了 %s", text)
