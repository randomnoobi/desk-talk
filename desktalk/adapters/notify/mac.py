"""
The Mac's own pop-up notifications, shown with the built-in `osascript` (nothing new to install).

show() never waits: it puts the pop-up in a small queue, and one background thread runs osascript for each one,
with a time limit (settings → notifications.time_limit_sec). When the queue is full the pop-up is dropped and
that is logged. A failure (osascript missing, slow, or macOS saying no) is only logged, once for each kind of
error, so the chat is never held up and the log doesn't fill.

On any other system, or with notifications switched off, make_notifications() gives NoNotifications.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from desktalk.config import NotificationsSettings

log = logging.getLogger(__name__)

MAC_PLATFORM = 'darwin'                 # what sys.platform says on a Mac
_STOP = object()                        # tells the background thread to finish


def applescript_string(text: str) -> str:
    """`text` as an AppleScript string in double quotes: backslashes and double quotes escaped, one line."""
    one_line = ' '.join(text.splitlines()) if text else ''
    return '"' + one_line.replace('\\', '\\\\').replace('"', '\\"') + '"'


class MacNotifications:
    """`runner` is subprocess.run (a test gives its own, so nothing really shows). `queue_size`: how many pop-ups
    can wait for the background thread before new ones are dropped."""

    def __init__(self, settings: NotificationsSettings, *, queue_size: int,
                 runner: Callable[..., Any] = subprocess.run):
        self._command = settings.command
        self._time_limit = settings.time_limit_sec
        self._runner = runner
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self._logged_errors: set[str] = set()
        self._thread = threading.Thread(target=self._work, name='desktop notifications', daemon=True)
        self._thread.start()

    def show(self, title: str, text: str) -> None:
        try:
            self._queue.put_nowait((title, text))
        except queue.Full:
            log.warning('Too many notifications waiting: one was dropped (%s)', title)

    def close(self) -> None:
        """Show what is waiting, then stop the background thread."""
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
            title, text = item
            self._run(title, text)

    def _run(self, title: str, text: str) -> None:
        script = f'display notification {applescript_string(text)} with title {applescript_string(title)}'
        try:
            self._runner([self._command, '-e', script], timeout=self._time_limit, check=True, capture_output=True)
        except Exception as error:      # noqa: BLE001  (any failure is only logged: the chat must go on)
            kind = type(error).__name__
            if kind not in self._logged_errors:
                self._logged_errors.add(kind)
                log.warning('Could not show a notification (%s: %s). This is only logged once for each kind '
                            'of error.', kind, error)


class NoNotifications:
    """Does nothing (not a Mac, or notifications are switched off)."""

    def show(self, title: str, text: str) -> None:
        return None


def make_notifications(settings: NotificationsSettings, platform: str, *, queue_size: int,
                       runner: Callable[..., Any] = subprocess.run) -> MacNotifications | NoNotifications:
    """The real notifications on a Mac when switched on; otherwise NoNotifications (saying so once if it's
    switched on but this isn't a Mac). `platform` is sys.platform."""
    if not settings.enabled:
        return NoNotifications()
    if platform != MAC_PLATFORM:
        log.info('Notifications only work on a Mac, so none will be shown on this computer.')
        return NoNotifications()
    return MacNotifications(settings, queue_size=queue_size, runner=runner)
