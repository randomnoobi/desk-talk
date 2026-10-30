"""
Turns "seen in this picture" into steady present / gone.

The roster uses it with long times (an agent joins after 3 s, leaves after 45 s) and the habit
tracker with short ones (a cup is gone after 2.5 s, so picking it up for a sip counts).
No camera, no threads, no clock: the caller passes the time of each picture.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping


class PresenceTracker:
    """A key becomes present after it has been seen for `appear_sec` without a gap longer than
    `gone_sec`, and gone after it has been missing for `gone_sec`."""

    def __init__(self, appear_sec: float, gone_sec: float):
        self.appear_sec = float(appear_sec)
        self.gone_sec = float(gone_sec)
        self._run_start: dict[Hashable, float] = {}    # key → when the current stretch of sightings began
        self._last_seen: dict[Hashable, float] = {}    # key → last time it was in a picture
        self._present: set[Hashable] = set()

    def update(self, seen_keys: Iterable[Hashable], now: float, *,
               waits: Mapping[Hashable, float] | None = None) -> tuple[list, list]:
        """One picture's keys. Returns (keys that just appeared, keys that just went). `waits`: keys that wait a
        different time than appear_sec before they appear."""
        seen = list(seen_keys)
        appeared, gone = [], []
        for k in seen:
            last = self._last_seen.get(k)
            if last is None or now - last > self.gone_sec:
                self._run_start[k] = now
            self._last_seen[k] = now
            wait = waits.get(k, self.appear_sec) if waits else self.appear_sec
            if k not in self._present and now - self._run_start[k] >= wait:
                self._present.add(k)
                appeared.append(k)
        seen_set = set(seen)
        for k in list(self._present):
            if k not in seen_set and now - self._last_seen.get(k, 0) >= self.gone_sec:
                self._present.discard(k)
                gone.append(k)
        return appeared, gone

    def is_present(self, key: Hashable) -> bool:
        return key in self._present

    def drop(self, key: Hashable) -> None:
        """Forget that `key` is present (it appears again on its next sighting)."""
        self._present.discard(key)

    def last_seen(self, key: Hashable) -> float | None:
        """When `key` was last in a picture (None if never)."""
        return self._last_seen.get(key)
