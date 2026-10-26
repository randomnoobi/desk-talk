"""Scene: what the camera sees on the desk right now (the roster and the producer write it)."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping

from desktalk.domain import SeenObject


class Scene:
    """Implements DeskScene. SeenObject is frozen, so handing out a copy of a dict of them is a full copy."""

    def __init__(self):
        self._lock = threading.Lock()
        self._members: dict[str, SeenObject] = {}     # agent id → its box (agents in the chat, in the picture)
        self._on_desk: dict[str, SeenObject] = {}     # agent id → its box (everything on the desk, chat or not)
        self._seen: dict[str, SeenObject] = {}        # every detector word in the newest picture → its best box
        self._first_seen: dict[str, float] = {}       # agent id → first time this session
        self._last_seen: dict[str, float] = {}
        self._person_at: float | None = None
        self._motion_at: float | None = None
        self._session_start: float | None = None      # when the camera started watching this session
        self._scan_labels: set[str] = set()           # what the last vision scan saw (also tags proof photos)

    def set_members(self, members: Mapping[str, SeenObject], now: float) -> None:
        with self._lock:
            self._members = dict(members)
            for agent_id in members:
                self._first_seen.setdefault(agent_id, now)
                self._last_seen[agent_id] = now

    def note_picture(self, seen: Mapping[str, SeenObject], now: float, *, person: bool, moved: bool,
                     new_session: bool) -> None:
        with self._lock:
            if new_session or self._session_start is None:
                self._session_start = now
            if person:
                self._person_at = now
            if moved:
                self._motion_at = now
            self._seen = dict(seen)

    def members(self) -> dict[str, SeenObject]:
        with self._lock:
            return dict(self._members)

    def set_on_desk(self, objects: Mapping[str, SeenObject], now: float) -> None:
        with self._lock:
            self._on_desk = dict(objects)

    def on_desk(self) -> dict[str, SeenObject]:
        with self._lock:
            return dict(self._on_desk)

    def seen(self) -> dict[str, SeenObject]:
        with self._lock:
            return dict(self._seen)

    def first_seen(self, agent_id: str) -> float | None:
        with self._lock:
            return self._first_seen.get(agent_id)

    def last_seen(self, agent_id: str) -> float | None:
        with self._lock:
            return self._last_seen.get(agent_id)

    def person_seen_at(self) -> float | None:
        with self._lock:
            return self._person_at

    def last_motion(self) -> float | None:
        with self._lock:
            return self._motion_at

    def session_started_at(self) -> float | None:
        with self._lock:
            return self._session_start

    def set_scan_labels(self, labels: Iterable[str]) -> None:
        with self._lock:
            self._scan_labels = set(labels)

    def scan_labels(self) -> set[str]:
        with self._lock:
            return set(self._scan_labels)
