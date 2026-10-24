"""The real clock: the computer's own time."""

from __future__ import annotations

import time

DAY_FORMAT = '%Y-%m-%d'          # the same day names the event log and the habit days use


class SystemClock:
    """The real Clock (desktalk/ports.py). Tests use tests/fakes/FakeClock instead."""

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def local_hour(self, now: float) -> int:
        """The hour (0-23) on this computer's clock at `now`."""
        return time.localtime(now).tm_hour

    def local_day(self, now: float) -> str:
        """The day at `now` on this computer's clock, e.g. '2026-10-24'."""
        return time.strftime(DAY_FORMAT, time.localtime(now))
