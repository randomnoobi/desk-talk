"""
A job that runs over and over in a thread of its own, and starts again after a crash.

    thread = WorkerThread('uploader', uploader.tick, Backoff(first=0.5, most=0.5), wake=uploader.wake)
    thread.start()
    thread.stop()      # at once, unless it's in the middle of a step

step() returns the seconds to wait before the next step, or None to wait until `wake` is set.
A step that raises is logged, and the next one comes after backoff.next() seconds (longer after each
crash in a row, up to the backoff's most); one good step resets the backoff. Standard library only.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from desktalk_shared.backoff import Backoff

log = logging.getLogger(__name__)


class WorkerThread(threading.Thread):
    def __init__(self, name: str, step: Callable[[], float | None], backoff: Backoff,
                 wake: threading.Event | None = None):
        super().__init__(name=name, daemon=True)
        self.step = step
        self.backoff = backoff
        self.wake = wake or threading.Event()      # set it to end the wait early
        self._stopping = False

    def run_once(self) -> float | None:
        """One step. Returns the wait before the next one (None: until woken)."""
        try:
            wait = self.step()
        except Exception:
            wait = self.backoff.next()
            log.exception('%s had a problem; trying again in %g s', self.name, wait)
            return wait
        self.backoff.reset()
        return wait

    def run(self) -> None:
        while not self._stopping:
            self.wake.clear()
            wait = self.run_once()
            if not self._stopping:
                self.wake.wait(wait)

    def stop(self) -> None:
        self._stopping = True
        self.wake.set()
