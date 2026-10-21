"""
DeskStatus: the questions the background jobs ask before speaking up.

    status.reminders_ready(now)   # the camera has watched a while, it's on, and it's not quiet hours
    status.user_active(now)       # a person or movement was seen lately (reminders.someone_here_sec)

The habit tracker has its own, shorter "someone is at the desk" window (presence.user_active_window_sec);
the two are kept apart on purpose.
"""

from __future__ import annotations

from desktalk.config import RemindersSettings
from desktalk.core.habits.preferences import quiet_hours_now
from desktalk.desk_state import DeskState
from desktalk.ports import FrameSource, Preferences

MIN = 60


class DeskStatus:
    """Implements the DeskStatus interface (desktalk/ports.py) from DeskState, the camera and the preferences."""

    def __init__(self, desk: DeskState, camera: FrameSource, prefs: Preferences, reminders: RemindersSettings):
        self._desk = desk
        self._camera = camera
        self._prefs = prefs
        self._warmup_sec = reminders.warmup_min * MIN
        self._someone_here_sec = reminders.someone_here_sec

    def camera_connected(self) -> bool:
        return bool(self._camera.connected)

    def warm(self, now: float) -> bool:
        """The camera has watched long enough for nudges (reminders.warmup_min)."""
        with self._desk.lock:
            start = self._desk.session_start
        return bool(start) and now - start >= self._warmup_sec

    def reminders_ready(self, now: float) -> bool:
        return self.warm(now) and self.camera_connected() and not quiet_hours_now(self._prefs, now)

    def user_active(self, now: float) -> bool:
        """Someone seems to be at the desk: a person or movement was seen lately."""
        with self._desk.lock:
            seen = (self._desk.person_last_seen, self._desk.last_motion_ts)
        return any(t and now - t < self._someone_here_sec for t in seen)

    def open_todos(self) -> list[str]:
        """Unfinished items on the to-do tracker(s) the app sent."""
        todos: list[str] = []
        with self._desk.lock:
            for widget in self._desk.char_widgets.values():
                if isinstance(widget, dict) and widget.get('type') == 'todo':
                    todos += [t.get('text', '') for t in widget.get('items', []) if not t.get('done')]
        return todos
