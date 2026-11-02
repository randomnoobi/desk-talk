"""
The Mac's own pop-up notifications, shown with the built-in `osascript` (nothing new to install), and the choice
between them and the speaker (make_notifications).

show() never waits (queued.py): one background thread runs osascript for each pop-up, with a time limit (settings →
notifications.time_limit_sec). A failure (osascript missing, slow, or macOS saying no) is only logged, once for each
kind of error, so the chat is never held up and the log doesn't fill.

With pop-ups on any other system, or with notifications switched off, make_notifications() gives NoNotifications.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Any

from desktalk.adapters.notify.queued import QueuedNotifications
from desktalk.adapters.notify.speaker import RunCommand, SpeakerNotifications
from desktalk.config import NotificationsSettings
from desktalk.ports import Clock

log = logging.getLogger(__name__)

MAC_PLATFORM = 'darwin'                 # what sys.platform says on a Mac


def applescript_string(text: str) -> str:
    """`text` as an AppleScript string in double quotes: backslashes and double quotes escaped, one line."""
    one_line = ' '.join(text.splitlines()) if text else ''
    return '"' + one_line.replace('\\', '\\\\').replace('"', '\\"') + '"'


class MacNotifications(QueuedNotifications):
    """`runner` is subprocess.run when not given (a test gives its own, so nothing really shows; tests/conftest.py
    makes sure of it). `queue_size`: how many pop-ups can wait for the background thread before new ones are
    dropped."""

    def __init__(self, settings: NotificationsSettings, *, queue_size: int,
                 runner: Callable[..., Any] | None = None):
        self._command = settings.command
        self._runner = runner if runner is not None else subprocess.run
        self._logged_errors: set[str] = set()
        super().__init__(queue_size=queue_size, time_limit_sec=settings.time_limit_sec)

    def _say(self, title: str, text: str) -> None:
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
    """Does nothing (notifications are off, or Mac pop-ups on another system)."""

    def show(self, title: str, text: str) -> None:
        return None


def make_notifications(settings: NotificationsSettings, platform: str, *, queue_size: int, clock: Clock,
                       runner: Callable[..., Any] | None = None,
                       run_command: RunCommand | None = None) -> QueuedNotifications | NoNotifications:
    """What settings → notifications.output asks for, when switched on: the speaker on any system, or pop-ups on a
    Mac. Otherwise NoNotifications (saying so once for pop-ups on another system). `platform` is sys.platform."""
    if not settings.enabled or settings.output == 'none':
        return NoNotifications()
    if settings.output == 'speaker':
        return SpeakerNotifications(settings, queue_size=queue_size, clock=clock, run_command=run_command)
    if platform != MAC_PLATFORM:
        log.info('Pop-ups only work on a Mac, so none will be shown here. notifications.output can be speaker or none.')
        return NoNotifications()
    return MacNotifications(settings, queue_size=queue_size, runner=runner)
