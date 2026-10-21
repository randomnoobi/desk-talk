"""
The watchdog: ends the camera program when no picture has come for a while (settings →
pi.restart_after_missed_pictures picture times), so systemd starts it again (Restart=always).

A camera that hangs inside capture() can't be stopped from Python, and the program would wait for good.
So a thread of its own checks, and when it's time it ends the whole process at once.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from raspberry_pi.camera.clock import Clock

log = logging.getLogger(__name__)

EXIT_STUCK = 3           # the exit code when the watchdog ends the program


def end_the_program() -> None:
    """Write out the log, then end the process now (the stuck camera thread can't be asked to stop)."""
    logging.shutdown()
    os._exit(EXIT_STUCK)


class Watchdog:
    """The camera's thread calls picture_taken() after each picture; a thread of its own calls tick()."""

    def __init__(self, limit_sec: float, clock: Clock, stop_now: Callable[[], None] = end_the_program):
        self.limit_sec = limit_sec
        self.clock = clock
        self.stop_now = stop_now
        self._last = clock.monotonic()

    def picture_taken(self) -> None:
        self._last = self.clock.monotonic()

    def tick(self) -> float:
        """Check once. Returns the seconds until the next check."""
        quiet = self.clock.monotonic() - self._last
        if quiet < self.limit_sec:
            return self.limit_sec - quiet
        log.error('No picture from the camera for %.0f s: it seems stuck. Stopping the camera program, so systemd '
                  'starts it again.', quiet)
        self.stop_now()
        return self.limit_sec
