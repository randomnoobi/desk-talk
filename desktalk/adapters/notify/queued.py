"""
What the pop-ups and the speaker have in common: show() never waits. It puts the message in a small queue and one
background thread deals with each in turn. When the queue is full a message is dropped and that is logged (which one
is the subclass's choice: _when_full). A problem with one message is logged, and the thread goes on to the next.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

log = logging.getLogger(__name__)

STOP = object()                         # tells the background thread to finish


class QueuedNotifications:
    """`queue_size`: how many can wait for the background thread before one is dropped. `time_limit_sec`: how long
    close() waits for each step of stopping. A subclass says what to do with each message in _say()."""

    def __init__(self, *, queue_size: int, time_limit_sec: float):
        self._time_limit = time_limit_sec
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._work, name='desktop notifications', daemon=True)
        self._thread.start()

    def show(self, title: str, text: str) -> None:
        self._add((title, text))

    def close(self) -> None:
        """Deal with what is waiting, then stop the background thread."""
        try:
            self._queue.put(STOP, timeout=self._time_limit)
        except queue.Full:
            return
        self._thread.join(timeout=self._time_limit)

    def _add(self, item: tuple[Any, ...]) -> None:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._when_full(item)

    def _when_full(self, item: tuple[Any, ...]) -> None:
        """The queue is full: the new one is dropped, unless a subclass says otherwise."""
        log.warning('Too many notifications waiting: one was dropped (%s)', item[0])

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is STOP:
                return
            try:
                self._deal_with(item)
            except Exception:           # noqa: BLE001  (one bad message must not end this thread for good)
                log.exception('Could not deal with a notification (%s)', item[0])

    def _deal_with(self, item: tuple[Any, ...]) -> None:
        self._say(*item)

    def _say(self, title: str, text: str) -> None:
        raise NotImplementedError
