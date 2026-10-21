"""The real clock: the computer's own time."""

from __future__ import annotations

import time


class SystemClock:
    """The real Clock (desktalk/ports.py). Tests use tests/fakes/FakeClock instead."""

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
