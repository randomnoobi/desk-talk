"""
How long to wait after each failure in a row: `first`, then twice that, and so on up to `most`.

    backoff = Backoff(first=1, most=10)
    backoff.next()     # 1, then 2, 4, 8, 10, 10 ...
    backoff.reset()    # it worked: the next failure waits `first` again

With `jitter` (a share from 0 to 1) each wait is cut by up to that share, by chance, so many things that failed
together don't all try again at the same moment. Jitter only ever shortens a wait, so `most` still holds, and the
doubling goes on from the plain wait, not the shortened one:

    Backoff(first=1, most=10, jitter=0.2).next()    # somewhere from 0.8 to 1, then 1.6 to 2, 3.2 to 4 ...

`jittered(wait, share, rng)` does the same to a single wait, for waits that don't double.

Used by the Pi's uploader and detector client, the detector container, Desk Talk's background jobs and the cloud
server. Standard library only.
"""

from __future__ import annotations

import random
from typing import Protocol


class Chance(Protocol):
    """What a Backoff needs from random.Random (tests pass a stand-in that gives set numbers)."""

    def random(self) -> float: ...


def jittered(wait: float, share: float, rng: Chance) -> float:
    """`wait` cut by up to `share` of it, by chance (share 0: `wait` as it is)."""
    if not share:
        return wait
    return wait * (1 - share * rng.random())


class Backoff:
    def __init__(self, first: float, most: float, *, jitter: float = 0.0, rng: Chance | None = None):
        if not 0 <= jitter <= 1:
            raise ValueError(f'jitter must be from 0 to 1 (got {jitter!r})')
        self.first = float(first)
        self.most = float(most)
        self.jitter = float(jitter)
        self._rng = rng or random.Random()
        self._last = 0.0                 # the last plain wait (before jitter; 0 = none since the last reset)

    def next(self) -> float:
        """The wait after one more failure."""
        # Doubled step by step (not 2 ** failures), so it never overflows however long it keeps failing.
        self._last = min(self._last * 2, self.most) if self._last else min(self.first, self.most)
        return jittered(self._last, self.jitter, self._rng)

    def reset(self) -> None:
        """It worked: start again from `first`."""
        self._last = 0.0
