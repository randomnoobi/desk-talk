"""Small stand-ins for what parts ask each other (roster, profiles, habits, preferences, desk status, the to-do
list, the chat's joins ...)."""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.domain import AgentProfile, ChatJoin, DeskSnapshot, HabitEvent, Nudge, ObjectSnapshot, StateChange, Todo
from desktalk.ports import EventBus
from desktalk.util import in_hour_window
from tests.fakes.clock import FakeClock


class StaticRoster:
    def __init__(self, ids: Sequence[str] = ()):
        self.ids = list(ids)

    def member_ids(self) -> list[str]:
        return list(self.ids)


class FakeProfiles:
    def __init__(self, profiles: Sequence[AgentProfile] = (), ignored: Sequence[str] = (),
                 risks: Mapping[tuple[str, str], str] | None = None):
        self.by_id = {p.id: p for p in profiles}
        self.ignored = set(ignored)
        self.risks = dict(risks or {})

    def get(self, agent_id: str) -> AgentProfile | None:
        return self.by_id.get(agent_id)

    def for_label(self, label: str) -> AgentProfile | None:
        if label in self.ignored:
            return None
        return next((p for p in self.by_id.values() if label in p.labels), None)

    def mention_words(self) -> dict[str, str]:
        """The same words as the real ProfileBook: id, name, nickname, object and labels (and a label's first
        part before a /), lower case, first agent wins."""
        words: dict[str, str] = {}
        for p in self.by_id.values():
            for word in [p.id, p.name, p.nickname, p.object, *p.labels]:
                word = str(word or '').lower().strip()
                if word:
                    words.setdefault(word, p.id)
                    words.setdefault(word.split('/')[0].strip(), p.id)
        return words

    def all(self) -> list[AgentProfile]:
        return list(self.by_id.values())

    def ids_for_habit(self, habit: str) -> list[str]:
        return [p.id for p in self.by_id.values() if habit in p.habits]

    def labels_for_habit(self, habit: str) -> set[str]:
        return {label for p in self.by_id.values() if habit in p.habits for label in p.labels}

    def is_ignored(self, label: str) -> bool:
        return label in self.ignored

    def relationship_risk(self, a: str, b: str) -> str | None:
        return self.risks.get((a, b)) or self.risks.get((b, a))

    def set_personality(self, agent_id: str, text: str) -> None:
        self.by_id[agent_id].personality = text
        self.by_id[agent_id].learned = True


class FakeHabits:
    def __init__(self, today: Mapping[str, Any] | None = None, nudges: Sequence[Nudge] = ()):
        self._today = dict(today or {})
        self._nudges = list(nudges)
        self.bumps: list[tuple[str, int]] = []
        self.fired: list[tuple[Nudge, float]] = []
        self.saves = 0

    def today(self, now: float) -> dict[str, Any]:
        return dict(self._today)

    def bump(self, key: str, amount: int = 1) -> None:
        self.bumps.append((key, amount))

    def nudges(self, now: float, open_todos: Sequence[Todo]) -> list[Nudge]:
        return list(self._nudges)

    def note_fired(self, nudge: Nudge, now: float) -> None:
        self.fired.append((nudge, now))

    def save(self, force: bool = False) -> None:
        self.saves += 1


class FakeObjectStates:
    """Objects and changes the test sets by hand. `snapshot_calls` counts how often snapshot() was asked."""

    def __init__(self, objects: Sequence[ObjectSnapshot] = (), watched_since: float | None = None):
        self.objects = {o.label: o for o in objects}
        self.watched_since = watched_since
        self.recorded: list[StateChange] = []
        self.snapshot_calls = 0

    def set(self, label: str, state: str, since: float | None) -> None:
        self.objects[label] = ObjectSnapshot(label, state, since)

    def change(self, ts: float, label: str, kind: str) -> None:
        self.recorded.append(StateChange(ts, label, kind))

    def snapshot(self) -> DeskSnapshot:
        self.snapshot_calls += 1
        return DeskSnapshot(dict(self.objects), self.watched_since)

    def changes(self, since: float) -> list[StateChange]:
        return sorted((c for c in self.recorded if c.ts > since), key=lambda c: c.ts)


class FakeRuleNudges:
    """The reminders the test hands out. `fired` records note_fired calls; `calls` counts nudges() calls."""

    def __init__(self, nudges: Sequence[Nudge] = ()):
        self._nudges = list(nudges)
        self.fired: list[tuple[Nudge, float]] = []
        self.calls = 0

    def nudges(self, now: float) -> list[Nudge]:
        self.calls += 1
        return list(self._nudges)

    def note_fired(self, nudge: Nudge, now: float) -> None:
        self.fired.append((nudge, now))


class FakePreferences:
    """`clock`: what quiet_now() reads the local hour from (UTC by default)."""

    def __init__(self, values: Mapping[str, Any] | None = None, clock: FakeClock | None = None):
        self.values = dict(values or {})
        self.clock = clock or FakeClock()

    def get(self, key: str) -> Any:
        return self.values.get(key)

    def all(self) -> dict[str, Any]:
        return dict(self.values)

    def update(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        self.values.update(changes)
        return dict(self.values)

    def quiet_now(self, now: float) -> bool:
        return bool(self.values.get('quiet_enabled')) and in_hour_window(
            self.clock.local_hour(now), int(self.values['quiet_start_hour']), int(self.values['quiet_end_hour']))


class FakeDeskStatus:
    """Every answer is a plain attribute the test sets."""

    def __init__(self, connected=True, warm=True, ready=True, active=True, todos: Sequence[Todo] = ()):
        self.connected, self.is_warm, self.ready, self.active, self.todos = connected, warm, ready, active, list(todos)

    def camera_connected(self) -> bool:
        return self.connected

    def warm(self, now: float) -> bool:
        return self.is_warm

    def reminders_ready(self, now: float) -> bool:
        return self.ready

    def user_active(self, now: float) -> bool:
        return self.active

    def open_todos(self) -> list[Todo]:
        return list(self.todos)


class FakeChatJoins:
    """The chat's joins, leaves and refusals the test hands it (or add()s later), kept in time order."""

    def __init__(self, joins: Sequence[ChatJoin] = ()):
        self._joins: list[ChatJoin] = []
        for join in joins:
            self.add(join)

    def add(self, join: ChatJoin) -> None:
        self._joins = sorted([*self._joins, join], key=lambda j: j.ts)

    def joins(self, label: str) -> list[ChatJoin]:
        return [j for j in self._joins if j.label == label]


def _a_day(due: str | None) -> str | None:
    """The due date if it's a real day like 2026-10-30, else None (as the real to-do list keeps it)."""
    try:
        return datetime.date.fromisoformat(due).isoformat() if due and len(due) == len('2026-10-30') else None
    except ValueError:
        return None


class FakeTodoList:
    """A to-do list kept in memory. With a `bus`, ticking a to-do off publishes the same HabitEvent the real one
    does (habit 'tasks', action 'done', extra {'task': text})."""

    def __init__(self, bus: EventBus | None = None, items: Sequence[Todo] | None = None):
        self._bus = bus
        self._items: list[Todo] = list(items or [])
        self._saved = items is not None

    def items(self) -> list[Todo]:
        return list(self._items)

    def replace(self, items: Sequence[Todo], now: float) -> list[Todo]:
        was_open = {t.text for t in self._items if not t.done}
        self._items = [Todo(t.text, t.done, _a_day(t.due)) for t in items]
        self._saved = True
        for todo in self._items:
            if todo.done and todo.text in was_open and self._bus is not None:
                self._bus.publish(HabitEvent(ts=now, habit='tasks', action='done', extra={'task': todo.text}))
        return list(self._items)

    def has_list(self) -> bool:
        return self._saved


class FakeChatVoice:
    def __init__(self, text: str | None = 'Time for a sip?'):
        self.text = text
        self.asked: list[tuple[str, Nudge]] = []

    def phrase_nudge(self, agent_id: str, nudge: Nudge) -> str | None:
        self.asked.append((agent_id, nudge))
        return self.text


class FakeCameraHealth:
    def __init__(self, messages: Sequence[Mapping[str, Any]] = ()):
        self.messages = [dict(m) for m in messages]
        self.checks: list[dict[str, Any]] = []

    def check(self, now: float, **kw) -> list[dict[str, Any]]:
        self.checks.append({'now': now, **kw})
        out, self.messages = self.messages, []
        return out


class FakePictureCheck:
    def __init__(self, moved: bool = False):
        self.moved = moved
        self.samples: list[tuple[Any, list, float]] = []

    def sample(self, picture: Any, detections: Sequence, now: float) -> None:
        self.samples.append((picture, list(detections), now))

    def moved_recently(self, now: float) -> bool:
        return self.moved


class FakePainter:
    def __init__(self):
        self.painted = 0

    def paint(self, picture: Any, detections: Sequence) -> Any:
        self.painted += 1
        return picture.copy()
