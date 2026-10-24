"""What the desk checks look at (Thing) and what they find (Findings)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from desktalk.core.vision.geometry import Box, Point
from desktalk.domain import SeenObject


@dataclass(frozen=True)
class Thing:
    """One agent's object on the desk."""

    id: str              # the agent id, e.g. "cup"
    label: str           # the detector's word, e.g. "wine glass"
    box: Box
    center: Point

    @classmethod
    def from_desk(cls, agent_id: str, obj: Mapping[str, Any]) -> Thing:
        """From DeskState.objects: {class_name, bbox, center, ...}."""
        return cls(agent_id, obj['class_name'], tuple(obj['bbox']), tuple(obj['center']))

    @classmethod
    def from_seen(cls, agent_id: str, seen: SeenObject) -> Thing:
        """From the desk scene (DeskScene.members)."""
        return cls(agent_id, seen.label, seen.box, seen.center)


@dataclass
class Findings:
    """What the checks found, in the shape the app and the AI have always read (DeskState.rel_result):
    risk events, relationships (what the live view draws) and explanations (plain sentences)."""

    risk_events: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    explanations: list[str] = field(default_factory=list)

    def add(self, event: dict[str, Any], relationship: dict[str, Any], explanation: str | None = None) -> None:
        self.risk_events.append(event)
        self.relationships.append(relationship)
        if explanation is not None:
            self.explanations.append(explanation)

    def __iadd__(self, other: Findings) -> Findings:
        self.risk_events += other.risk_events
        self.relationships += other.relationships
        self.explanations += other.explanations
        return self
