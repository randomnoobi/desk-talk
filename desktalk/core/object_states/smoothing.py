"""
Smoothing: from confidence numbers to a steady yes / no.

    Hysteresis       two lines instead of one, so a wobbly confidence doesn't flicker
    MajorityWindow   the last few pictures vote on "seen" and give a steady position

Pure code: no clock, no disk. The numbers come from the caller.
"""

from __future__ import annotations

from collections import deque
from statistics import median


class Hysteresis:
    """Turns on at the high line and stays on until the confidence drops below the low line."""

    def __init__(self, on: float, off: float) -> None:
        self._on = on
        self._off = off
        self._is_on = False

    def update(self, conf: float | None) -> bool:
        """Feed one picture's confidence (None when the object wasn't found, which counts as 0)."""
        value = 0.0 if conf is None else conf
        if self._is_on:
            if value < self._off:
                self._is_on = False
        elif value >= self._on:
            self._is_on = True
        return self._is_on


class MajorityWindow:
    """The last `size` pictures vote on "seen"; the seen ones also give a steady position."""

    def __init__(self, size: int, vote_share: float) -> None:
        self._vote_share = vote_share
        self._pictures: deque[tuple[bool, tuple[float, float] | None, float | None]] = deque(maxlen=size)

    def add(
        self, seen: bool, center: tuple[float, float] | None, conf: float | None
    ) -> tuple[bool, tuple[float, float] | None, float | None]:
        """Add one picture. Returns (majority says seen, median center, average confidence).

        Yes votes must be more than `vote_share` of the pictures in the window (so far, until it fills).
        Center and confidence only use the pictures where it was seen; None if there were none.
        """
        self._pictures.append((seen, center, conf))
        yes = sum(1 for was_seen, _, _ in self._pictures if was_seen)
        majority = yes > self._vote_share * len(self._pictures)

        centers = [c for was_seen, c, _ in self._pictures if was_seen and c is not None]
        confs = [v for was_seen, _, v in self._pictures if was_seen and v is not None]
        middle = (median(x for x, _ in centers), median(y for _, y in centers)) if centers else None
        average = sum(confs) / len(confs) if confs else None
        return majority, middle, average
