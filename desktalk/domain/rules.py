"""What the rule engine reads: where each object on the desk is, what changed lately, what already nudged."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ObjectSnapshot:
    """One object right now."""

    label: str
    state: str                  # PRESENT | ABSENT | UNKNOWN (the state machine's own words)
    since: float | None         # when it got into this state (None = not known)


@dataclass(frozen=True)
class DeskSnapshot:
    """The desk right now."""

    objects: Mapping[str, ObjectSnapshot] = field(default_factory=dict)
    watched_since: float | None = None   # when the camera's current run of pictures began (None = no pictures yet)


@dataclass(frozen=True)
class StateChange:
    """Something on the desk changed."""

    ts: float
    label: str
    change: str                 # appeared | removed | moved


@dataclass(frozen=True)
class RuleHistory:
    """What a rule may look back on."""

    changes: Sequence[StateChange] = ()                                   # oldest first
    fired: Mapping[str, Sequence[float]] = field(default_factory=dict)    # rule id → when it nudged, oldest first
