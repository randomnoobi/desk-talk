"""The time. Every part asks a clock instead of calling time.time(), so tests can move time by hand."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Seconds since 1970 (the Pi's own clock): for when a picture was taken."""
        ...

    def monotonic(self) -> float:
        """Seconds that only ever go up: for waits and how long something took."""
        ...

    def sleep(self, seconds: float) -> None:
        ...


class SystemClock:
    """The Pi's own clock."""

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
