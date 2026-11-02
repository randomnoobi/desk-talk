"""
What the pop-ups and the speaker have in common: show() never waits. It puts the message in a small queue and one
background thread deals with each in turn. When the queue is full the message is dropped and that is logged.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

log = logging.getLogger(__name__)

_STOP = object()                        # tells the background thread to finish


class QueuedNotifications:
    """`queue_size`: how many can wait for the background thread before new ones are dropped. `time_limit_sec`: how
    long close() waits for each step of stopping. A subclass says what to do with each message in _say()."""

    def __init__(self, *, queue_size: int, time_limit_sec: float):
        self._time_limit = time_limit_sec
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._work, name='desktop notifications', daemon=True)
        self._thread.start()

    def show(self, title: str, text: str) -> None:
        try:
            self._queue.put_nowait((title, text))
        except queue.Full:
            log.warning('Too many notifications waiting: one was dropped (%s)', title)

    def close(self) -> None:
        """Deal with what is waiting, then stop the background thread."""
        try:
            self._queue.put(_STOP, timeout=self._time_limit)
        except queue.Full:
            return
        self._thread.join(timeout=self._time_limit)

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            self._say(*item)

    def _say(self, title: str, text: str) -> None:
        raise NotImplementedError
