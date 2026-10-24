"""Small stand-ins for what parts ask each other (roster, profiles, habits, preferences, desk status ...)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.domain import AgentProfile, Nudge
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

    def nudges(self, now: float, open_todos: Sequence[str]) -> list[Nudge]:
        return list(self._nudges)

    def note_fired(self, nudge: Nudge, now: float) -> None:
        self.fired.append((nudge, now))

    def save(self, force: bool = False) -> None:
        self.saves += 1


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

    def __init__(self, connected=True, warm=True, ready=True, active=True, todos: Sequence[str] = ()):
        self.connected, self.is_warm, self.ready, self.active, self.todos = connected, warm, ready, active, list(todos)

    def camera_connected(self) -> bool:
        return self.connected

    def warm(self, now: float) -> bool:
        return self.is_warm

    def reminders_ready(self, now: float) -> bool:
        return self.ready

    def user_active(self, now: float) -> bool:
        return self.active

    def open_todos(self) -> list[str]:
        return list(self.todos)


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
