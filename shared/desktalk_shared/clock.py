"""
The real clock: this machine's own time. It's the one Clock that both the Pi's camera program and
Desk Talk on the computer use (desktalk/ports.py says what a Clock does). Tests use a fake clock instead.
Standard library only.
"""

from __future__ import annotations

import time

DAY_FORMAT = '%Y-%m-%d'          # the same day names the event log and the habit days use


class SystemClock:
    def now(self) -> float:
        """Seconds since 1970 (wall clock)."""
        return time.time()

    def monotonic(self) -> float:
        """Seconds that only ever go up: for waits and how long something took."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def local_hour(self, now: float) -> int:
        """The hour (0-23) on this machine's clock at `now`."""
        return time.localtime(now).tm_hour

    def local_day(self, now: float) -> str:
        """The day at `now` on this machine's clock, e.g. '2026-10-24'."""
        return time.strftime(DAY_FORMAT, time.localtime(now))

    def utc_offset_sec(self, now: float) -> float:
        """How far this machine's clock is ahead of UTC at `now`, in seconds (summer time included)."""
        return float(time.localtime(now).tm_gmtoff)
