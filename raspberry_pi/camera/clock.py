"""The time. Every part asks a clock instead of calling time.time(), so tests can move time by hand."""

from __future__ import annotations

from typing import Protocol

from desktalk_shared.clock import SystemClock

__all__ = ['Clock', 'SystemClock']


class Clock(Protocol):
    def now(self) -> float:
        """Seconds since 1970 (the Pi's own clock): for when a picture was taken."""
        ...

    def monotonic(self) -> float:
        """Seconds that only ever go up: for waits and how long something took."""
        ...

    def sleep(self, seconds: float) -> None:
        ...
