"""
What the app, the Pi and the phone send in a request body, and the replies the web layer makes
itself. Fields the routes don't read are ignored, as before. Values the parts check themselves (times,
corners, to-do lists) are taken as they come, so their own plain-words errors reach the app.

A body that doesn't fit gets 400 {"error": "<field>: <what's wrong>"} (desktalk/web/app.py).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    """POST /api/chat"""

    chat: str = 'group'                     # 'group' or an agent id
    message: str | None = None
    recent_messages: list[Any] | None = None    # the earlier turns, newest last
    history: list[Any] | None = None            # the same, from an older app version
    widget: Any = None                          # the agent's to-do list, with a 1-on-1 message


class PanelList(BaseModel):
    """POST /api/chat_todos: one agent's to-do list."""

    chat: str | None = None
    widget: Any = None


class PanelLists(BaseModel):
    """POST /api/chat_todos_sync: every agent's to-do list (agent id → list)."""

    by_chat: dict[str, Any] = {}


class PanelEvent(BaseModel):
    """POST /api/widget_event: the user opened (or used) an agent's panel."""

    chat: str | None = None
    action: Any = 'view'


class HabitEntry(BaseModel):
    """POST /api/habits/log. Anything else sent (ml, minutes, label, healthy, index ...) goes to the tracker."""

    model_config = ConfigDict(extra='allow')

    habit: str = ''
    action: str = ''

    def details(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


OLD_PREFERENCE_NAMES = {'dnd_enabled': 'quiet_enabled', 'dnd_start_hour': 'quiet_start_hour',
                        'dnd_end_hour': 'quiet_end_hour'}


class PreferenceChanges(BaseModel):
    """PUT /api/preferences. Left out or null = unchanged. PreferencesFile checks the values."""

    wake_time: Any = None
    bed_time: Any = None
    quiet_enabled: Any = None
    quiet_start_hour: Any = None
    quiet_end_hour: Any = None
    dnd_enabled: Any = None              # the old names, from the previous app version
    dnd_start_hour: Any = None
    dnd_end_hour: Any = None

    def changes(self) -> dict[str, Any]:
        """The new names only (an old name wins over its new one, as before)."""
        out = self.model_dump(exclude_unset=True)
        for old, new in OLD_PREFERENCE_NAMES.items():
            if old in out:
                out[new] = out.pop(old)
        return out


class RuleChanges(BaseModel):
    """PUT /api/rules from the Settings page: only the parts sent are changed."""

    danger_zones: Any = None
    edge_proximity: Any = None
    chain_reaction: Any = None
    temporal: Any = None
    desk_outline: Any = None             # [[x, y], ...], each 0-1 of the picture (SafetyRules checks them)

    def sent(self) -> set[str]:
        return set(self.model_fields_set)


# ── replies ──
class Ok(BaseModel):
    ok: bool = True


class PiFrameReply(BaseModel):
    ok: bool = True
    fps: float
    tip: str | None                      # always sent, null when there's nothing to say


class PhoneFrameReply(BaseModel):
    ok: bool = True
    fps: float
    tip: str | None = None               # left out when there's nothing to say (shown on the phone, e.g. "Too dark")
