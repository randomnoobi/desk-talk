from __future__ import annotations


class FakeClock:
    """A clock that only moves when the test says so (advance) or when code calls sleep()."""

    def __init__(self, start: float = 1_760_000_000.0):
        self._now = float(start)
        self._monotonic = 0.0
        self.slept: list[float] = []      # every sleep() asked for, in order

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now += seconds
        self._monotonic += seconds
