"""
DeskStatusService: the questions the background jobs ask before speaking up.

    status.reminders_ready(now)   # the camera has watched a while, it's on, and it's not quiet hours
    status.user_active(now)       # you're at the desk: the person's object state is PRESENT

The person's object state is the on/off switch for reminders, small talk and vision checks. It turns on after
you've been seen for a few seconds (object_states.person_appear_sec) and off after you've been gone a while
(object_states.person_gone_sec). The habit tracker has its own "someone is at the desk" window
(presence.user_active_window_sec) for desk time and breaks; the two are kept apart on purpose.
"""

from __future__ import annotations

from typing import Protocol

from desktalk.config import RemindersSettings
from desktalk.core.habits.preferences import quiet_hours_now
from desktalk.domain import Phase, Todo
from desktalk.ports import CharacterMemory, Clock, DeskScene, FrameSource, ObjectStatesView, Preferences

MIN = 60


class SceneAndPanels(DeskScene, CharacterMemory, Protocol):
    """What the status asks the desk: the scene (when the camera started) and the tracker panels the app sent
    (the to-do list)."""


class DeskStatusService:
    """Implements the DeskStatus interface (desktalk/ports.py) from the desk, the camera, the preferences and
    the object states. person_label: the detector's word for you (object_states.person_label)."""

    def __init__(self, desk: SceneAndPanels, camera: FrameSource, prefs: Preferences, reminders: RemindersSettings,
                 states: ObjectStatesView, *, person_label: str, clock: Clock):
        self._desk = desk
        self._clock = clock
        self._camera = camera
        self._prefs = prefs
        self._warmup_sec = reminders.warmup_min * MIN
        self._states = states
        self._person_label = person_label

    def camera_connected(self) -> bool:
        return bool(self._camera.connected)

    def warm(self, now: float) -> bool:
        """The camera has watched long enough for nudges (reminders.warmup_min)."""
        start = self._desk.session_started_at()
        return bool(start) and now - start >= self._warmup_sec

    def reminders_ready(self, now: float) -> bool:
        return self.warm(now) and self.camera_connected() and not quiet_hours_now(self._prefs, now, self._clock)

    def user_active(self, now: float) -> bool:
        """You're at the desk: the person's object state is PRESENT. Not seen yet, or UNKNOWN, counts as away."""
        person = self._states.snapshot().objects.get(self._person_label)
        return person is not None and person.state == Phase.PRESENT

    def open_todos(self) -> list[Todo]:
        """Unfinished items on the to-do tracker(s) the app sent."""
        todos: list[Todo] = []
        for widget in self._desk.widgets().values():
            if widget.get('type') == 'todo':
                todos += [Todo(text=t.get('text', '')) for t in widget.get('items', []) if not t.get('done')]
        return todos
