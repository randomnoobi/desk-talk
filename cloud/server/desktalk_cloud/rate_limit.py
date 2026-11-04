"""Counts tries per key (an address, a Pi) in a sliding window, so a guesser or a runaway Pi is slowed down."""

from __future__ import annotations

import threading
from collections import deque

from desktalk_cloud.ports import Clock


class RateLimiter:
    """hit(key) is True while the key has made fewer than `limit` allowed tries in the last `window_sec`.
    A refused try is not counted, so the wait ends `window_sec` after the last allowed try."""

    def __init__(self, limit: int, window_sec: float, clock: Clock):
        self._limit = limit
        self._window = window_sec
        self._clock = clock
        self._tries: dict[str, deque[float]] = {}
        self._swept_at = clock.now()
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = self._clock.now()
        with self._lock:
            if now - self._swept_at >= self._window:
                self._sweep(now)
            tries = self._tries.setdefault(key, deque())
            while tries and now - tries[0] >= self._window:
                tries.popleft()
            if len(tries) >= self._limit:
                return False
            tries.append(now)
            return True

    def size(self) -> int:
        """How many keys it remembers (for tests)."""
        with self._lock:
            return len(self._tries)

    def _sweep(self, now: float) -> None:
        """Forget keys that have no try left in the window, so the table can't grow without end."""
        self._swept_at = now
        for key in [k for k, tries in self._tries.items() if not tries or now - tries[-1] >= self._window]:
            del self._tries[key]
