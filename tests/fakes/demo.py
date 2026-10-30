"""Stand-ins for the parts behind the developer page's Demo tab (desktalk/ports.py: ScenarioFirer, CameraWarnings,
ReminderSender, HabitLog). Each only records what it was asked to do. The first three pass the same contracts as
the real ones (tests/contracts/demo_parts_contract.py)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from desktalk.domain import Nudge


class RecordingFirer:
    """Records what it fired. `problems` names the scenarios it can't fire, with why (fire() refuses them with a
    ValueError, as the real one does); `broken` names one whose fire() fails the way a chat that's down would."""

    def __init__(self, broken: str = '', problems: Mapping[str, str] | None = None):
        self.fired: list[tuple[str, float]] = []
        self.broken = broken
        self.problems = dict(problems or {})

    def problem(self, name: str) -> str | None:
        return self.problems.get(name)

    def fire(self, name: str, now: float) -> None:
        problem = self.problem(name)
        if problem is not None:
            raise ValueError(problem)
        if name == self.broken:
            raise RuntimeError('the chat is down')
        self.fired.append((name, now))


class RecordingCameraWarnings:
    """Records each camera problem said, as (key, text, now)."""

    def __init__(self):
        self.said: list[tuple[str, str, float]] = []

    def say_problem(self, key: str, text: str, now: float) -> None:
        self.said.append((key, text, now))


class RecordingReminders:
    """Records each nudge said, as (nudge, now)."""

    def __init__(self):
        self.said: list[tuple[Nudge, float]] = []

    def say(self, nudge: Nudge, now: float) -> None:
        self.said.append((nudge, now))


class RecordingHabitLog:
    """Records each habit action logged, as (habit, action, details)."""

    def __init__(self):
        self.logged: list[tuple[str, str, dict[str, Any]]] = []

    def log(self, habit: str, action: str, **details: Any) -> None:
        self.logged.append((habit, action, details))
