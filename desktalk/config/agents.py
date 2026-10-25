"""The characters in config/agents.yaml. What each setting means is in that file's comments."""

from __future__ import annotations

from typing import Literal

from desktalk.config.base import Fraction, Words, _Section

Color = Literal['cobalt', 'teal', 'tang', 'mustard', 'coral', 'plum', 'moss', 'slate']
Habit = Literal['hydration', 'breaks', 'light', 'phone_free', 'screens', 'food', 'tasks']
Tracker = Literal['hydration', 'breaks', 'food', 'todo', 'phone_free', 'light']


class AgentDefaults(_Section):
    """The `default` block: used for any object without its own block."""

    personality: str
    temperature: Fraction
    icon: str
    too_close: str
    hello: str
    about: str                # {object} is filled in


class Agent(_Section):
    """One character. Anything left out comes from `default` (or is simply not used)."""

    name: str | None = None
    object: str | None = None
    nickname: str | None = None
    labels: Words
    icon: str | None = None
    art: str | None = None
    color: Color | None = None
    about: str | None = None
    personality: str | None = None
    temperature: Fraction | None = None
    habits: tuple[Habit, ...] = ()
    tracker: Tracker | None = None
    fragile: bool = False
    min_confidence: Fraction | None = None
    too_close: str | None = None
    hello: str | None = None


class Relationship(_Section):
    between: tuple[str, str]
    risk: str


class AgentsFile(_Section):
    default: AgentDefaults
    ignore: Words
    agents: dict[str, Agent]
    relationships: tuple[Relationship, ...]
