"""The real clock. Tests use tests.fakes.FakeClock, which moves only when the test says."""

from __future__ import annotations

import time


class SystemClock:
    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()
