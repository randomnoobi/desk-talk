"""Memories: what the characters said lately, and what the app told us about you (typing, tracker panels)."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any


class Memories:
    """Implements CharacterMemory."""

    def __init__(self):
        self._lock = threading.Lock()
        self._said: dict[str, list[str]] = {}                 # agent id → what it said lately, oldest first
        self._widgets: dict[str, dict[str, Any]] = {}         # agent id → tracker data sent by the app (to-do list)
        self._views: dict[str, list[dict[str, Any]]] = {}     # agent id → [{'ts', 'action'}]
        self._user_active_at = 0.0                            # you typing / sending → small talk waits
        self._vision_brief = ''                               # the last posture check in a few words

    def remember(self, agent_id: str, text: str, keep: int) -> None:
        with self._lock:
            lines = [*self._said.get(agent_id, []), text]
            self._said[agent_id] = lines[-keep:] if keep > 0 else []

    def recent(self, agent_id: str) -> list[str]:
        with self._lock:
            return list(self._said.get(agent_id, []))

    def set_widget(self, agent_id: str, data: Mapping[str, Any]) -> None:
        with self._lock:
            self._widgets[agent_id] = dict(data)

    def widget(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._widgets.get(agent_id)
            return dict(data) if data is not None else None

    def widgets(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {agent_id: dict(data) for agent_id, data in self._widgets.items()}

    def note_widget_view(self, agent_id: str, action: str, now: float, keep: int) -> None:
        with self._lock:
            views = [*self._views.get(agent_id, []), {'ts': now, 'action': action}]
            self._views[agent_id] = views[-keep:] if keep > 0 else []

    def widget_views_of(self, agent_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._views.get(agent_id, [])]

    def note_user_activity(self, now: float) -> None:
        with self._lock:
            self._user_active_at = now

    def user_active_at(self) -> float:
        with self._lock:
            return self._user_active_at

    def set_vision_brief(self, text: str) -> None:
        with self._lock:
            self._vision_brief = text

    def vision_brief(self) -> str:
        with self._lock:
            return self._vision_brief
