"""
Deletes the pictures stored on the Pi, in a thread of its own, so taking pictures never waits for it
(on a slow SD card, a cleanup of a big folder can take many seconds).

It deletes them every pi.delete_every_min (0 = never), and right away, oldest first, when the camera
loop says the disk is nearly full (pi.min_free_mb). A cleanup that fails is logged once, until it works
again, and tried again at the next turn.
"""

from __future__ import annotations

import logging
import threading

from raspberry_pi.camera.clock import Clock
from raspberry_pi.camera.photo_store import PhotoStore

log = logging.getLogger(__name__)


class Cleaner:
    """A WorkerThread calls tick() over and over; the camera's thread only calls need_space()."""

    def __init__(self, store: PhotoStore, delete_every_min: float, clock: Clock):
        self.store = store
        self.delete_every_min = delete_every_min
        self.clock = clock
        self.wake = threading.Event()        # set when the disk is nearly full: the thread stops waiting
        self._last_try = clock.now()
        self._space_needed = False
        self._problem = ''

    def need_space(self) -> None:
        """The disk is nearly full: delete the oldest pictures as soon as possible (called from the camera's thread)."""
        self._space_needed = True
        self.wake.set()

    def tick(self) -> float | None:
        """Free up space if asked, and clean up when it's time. Returns the seconds until the next cleanup
        (None: never on a schedule, only when the disk gets nearly full)."""
        if self._space_needed:
            self._space_needed = False
            removed = self.store.free_up_space()
            log.warning('Disk nearly full: deleted the %d oldest pictures', removed)
        self.cleanup_safely(self.clock.now())
        every = self.delete_every_min * 60
        if every <= 0:
            return None
        return min(every, max(0.0, self._last_try + every - self.clock.now()))

    def maybe_cleanup(self, now: float) -> int | None:
        """Delete the stored pictures every pi.delete_every_min (0 = never). Returns how many, or None if it's
        not time yet. A cleanup that fails waits for the next turn too."""
        every = self.delete_every_min * 60
        if every <= 0 or now - self._last_try < every:
            return None
        self._last_try = now
        removed = self.store.cleanup(now)
        log.info('Cleanup: deleted %d pictures (every %g min). %d MB free.', removed, self.delete_every_min,
                 self.store.free_mb())
        return removed

    def cleanup_safely(self, now: float) -> None:
        """maybe_cleanup(), but a problem is logged (once, until it goes away) and the next turn tries again."""
        try:
            removed = self.maybe_cleanup(now)
        except Exception as exc:
            problem = str(exc) or type(exc).__name__
            if problem != self._problem:
                log.warning('Problem deleting the stored pictures (the camera carries on): %s', problem)
                self._problem = problem
            return
        if removed is not None:
            self._problem = ''
