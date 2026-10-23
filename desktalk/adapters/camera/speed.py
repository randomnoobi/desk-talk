"""How many pictures a second a camera has been sending lately (shown in the app)."""

from __future__ import annotations


class SpeedMeter:
    """Smoothed pictures-a-second. `smoothing` (0-1) is how much each new gap changes the number;
    gaps longer than `gap_max_sec` (the camera paused) don't count."""

    def __init__(self, smoothing: float, gap_max_sec: float):
        self._smoothing = smoothing
        self._gap_max = gap_max_sec
        self._last: float | None = None
        self._fps = 0.0

    def picture_at(self, t: float) -> None:
        """A picture came in at `t` (seconds, any clock that only goes up)."""
        if self._last is not None:
            gap = t - self._last
            if 0 < gap < self._gap_max:
                rate = 1.0 / gap
                self._fps = (1 - self._smoothing) * self._fps + self._smoothing * rate if self._fps else rate
        self._last = t

    @property
    def fps(self) -> float:
        return round(self._fps, 1)
