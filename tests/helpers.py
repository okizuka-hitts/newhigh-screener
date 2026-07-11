"""テスト用の共有ヘルパ。"""


class FakeClock:
    """注入用の決定的な時計。sleep で時刻が進む。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
