"""
What the app, the Pi and the phone send in a request body, and the replies the web layer makes
itself. Fields the routes don't read are ignored, as before. Values the parts check themselves (times,
corners, to-do lists) are taken as they come, so their own plain-words errors reach the app.

A body that doesn't fit gets 400 {"error": "<field>: <what's wrong>"} (desktalk/web/app.py).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from desktalk.config import BreaksRule, EdgeProximity, MorningMinutes, PageRefresh, QuietHours, SettingsSliders


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
    sensitivity: int | None = None       # a step of the sensitivity slider (settings → agents.sensitivity_steps)
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


class RunRequest(BaseModel):
    """POST /api/dev/runs: {"check": true} for "Run everything", or {"tests": [ids]} for some tests."""

    check: bool = False
    tests: list[str] | None = None


class QueryRequest(BaseModel):
    """POST /api/dev/db/{name}/query"""

    sql: str


class PiFrameReply(BaseModel):
    ok: bool = True
    fps: float
    tip: str | None                      # always sent, null when there's nothing to say


class PhoneFrameReply(BaseModel):
    ok: bool = True
    fps: float
    tip: str | None = None               # left out when there's nothing to say (shown on the phone, e.g. "Too dark")


class PhoneSettings(BaseModel):
    """GET /api/phone/config, and the phone part of GET /api/ui_settings (settings → camera)."""

    fps: float                           # pictures a second to send
    max_side: int                        # longest side of each picture, in pixels
    jpeg_quality: int                    # 1-100
    tilt_max: float                      # a tilt reading above this (m/s², any axis) isn't a real one
    capture_width: int                   # the picture size the phone page asks its camera for
    capture_height: int


# ── GET /api/ui_settings: what the app page (web_app/) used to keep its own copies of ──
class UiPage(BaseModel):
    """settings → web: how often the page refreshes, how long it waits, how long lists are, its colors."""

    refresh_ms: PageRefresh
    request_timeout_ms: int
    live_timeout_ms: int
    activity_ping_ms: int
    toast_ms: int
    log_shown: int                       # web.list_sizes.log
    photos_shown: int                    # web.list_sizes.photos
    habit_log_shown: int                 # web.list_sizes.habit_log.default
    chat_kept: int                       # web.list_sizes.chat_kept: messages kept per chat...
    chat_saved: int                      # ...and saved in the browser (web.list_sizes.chat_saved)
    left_chat_shown_min: float           # a chat whose object left stays in the list this long
    settings_sliders: SettingsSliders    # the Settings page's slider ranges, in pixels...
    new_zone_px: int                     # ...and where a new danger zone's slider starts
    palette: list[str]


class UiChat(BaseModel):
    """settings → chat: what the page sends along with your message."""

    message_max_chars: int
    history_from_app: int
    todo_max_items: int
    todo_max_chars: int


class UiHabits(BaseModel):
    """The habit buttons and the Protocols numbers the Habits page shows."""

    sunlight_default_min: float          # settings → habit_tracker
    glass_ml: float
    food_label_max_chars: int
    goal_window_hours: float             # protocols.yaml → hydration
    stop_before_bed_hours: float
    morning_minutes: MorningMinutes      # protocols.yaml → light
    morning_window_min: float
    evening_dim_before_bed_min: float
    breaks: BreaksRule                   # protocols.yaml → breaks


class UiDesk(BaseModel):
    """settings → safety: what the Settings page needs to mark the desk."""

    edge_proximity: EdgeProximity
    desk_border_share: float
    min_desk_corners: int
    max_desk_corners: int


class UiPi(BaseModel):
    fps: float
    delete_every_min: float
    compact_every_min: float             # settings → event_log: how often pictures become intervals


class UiLensCard(BaseModel):
    id: str
    name: str
    art: str
    color: str
    about: str


class UiSettings(BaseModel):
    web: UiPage
    chat: UiChat
    habits: UiHabits
    desk: UiDesk
    quiet_hours: QuietHours              # settings → reminders.quiet_hours (the app's own choice wins)
    pi: UiPi
    phone: PhoneSettings
    lens_card: UiLensCard
    # How long the page waits for a chat reply: as long as the server may keep trying the AI (llm.longest_reply_sec)
    # for each AI call one message can make (routers/settings.py: most_ai_calls_per_message), plus an ordinary
    # request's wait for the trip there and back.
    chat_reply_timeout_ms: int
