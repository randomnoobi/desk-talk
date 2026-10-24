from __future__ import annotations

import time

DAY_FORMAT = '%Y-%m-%d'


class FakeClock:
    """A clock that only moves when the test says so (advance) or when code calls sleep().

    Its local time is a fixed number of hours from UTC (`utc_offset_hours`, 0 unless the test says), so a
    test gets the same hour and day on every computer."""

    def __init__(self, start: float = 1_760_000_000.0, *, utc_offset_hours: float = 0.0):
        self._now = float(start)
        self._monotonic = 0.0
        self._offset_sec = utc_offset_hours * 3600
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

    def local_hour(self, now: float) -> int:
        return time.gmtime(now + self._offset_sec).tm_hour

    def local_day(self, now: float) -> str:
        return time.strftime(DAY_FORMAT, time.gmtime(now + self._offset_sec))
