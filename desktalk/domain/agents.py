"""An agent: a detected object that joins the group chat as a character (config/agents.yaml)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

LENS = 'lens'           # Lens, the camera's own character: speaks when no agent fits
HABITS = ('hydration', 'breaks', 'light', 'phone_free', 'screens', 'food', 'tasks')


@dataclass
class AgentProfile:
    id: str
    name: str
    labels: list = field(default_factory=list)
    object: str = ''               # what it is, for the AI ("Cup", "stapler")
    nickname: str = ''
    icon: str = '✨'
    art: str | None = None
    color: str = 'plum'
    about: str = ''
    personality: str = ''
    temperature: float = 0.8
    habits: list = field(default_factory=list)
    tracker: str | None = None
    fragile: bool = False
    min_confidence: float | None = None
    too_close: str = ''
    hello: str = ''
    auto: bool = False             # made on the fly for an object without its own block
    learned: bool = False          # personality written by the AI

    def public(self) -> dict[str, Any]:
        """What the app needs to draw this agent."""
        d = asdict(self)
        for private in ('personality', 'temperature', 'too_close', 'hello', 'min_confidence'):
            d.pop(private, None)
        return d

    def too_close_text(self, other_name: str) -> str:
        return (self.too_close or '{other} is too close. Could you move it back?').format(other=other_name)

    def hello_text(self) -> str:
        return (self.hello or '').format(object=self.object or self.name)
