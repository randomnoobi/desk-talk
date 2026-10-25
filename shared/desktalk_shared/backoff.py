"""
How long to wait after each failure in a row: `first`, then twice that, and so on up to `most`.

    backoff = Backoff(first=1, most=10)
    backoff.next()     # 1, then 2, 4, 8, 10, 10 ...
    backoff.reset()    # it worked: the next failure waits `first` again

Used by the Pi's uploader and detector client and by Desk Talk's background jobs. Standard library only.
"""

from __future__ import annotations


class Backoff:
    def __init__(self, first: float, most: float):
        self.first = float(first)
        self.most = float(most)
        self._last = 0.0                 # the last wait handed out (0 = none since the last reset)

    def next(self) -> float:
        """The wait after one more failure."""
        # Doubled step by step (not 2 ** failures), so it never overflows however long it keeps failing.
        self._last = min(self._last * 2, self.most) if self._last else min(self.first, self.most)
        return self._last

    def reset(self) -> None:
        """It worked: start again from `first`."""
        self._last = 0.0
