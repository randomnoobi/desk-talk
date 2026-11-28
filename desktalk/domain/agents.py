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


@dataclass(frozen=True)
class ChatJoin:
    """One time an object joined the chat, left it, or was refused (the developer page's trace says why)."""

    agent: str                 # the agent's id (computer, cup ...)
    label: str                 # the detector's word for what was seen (laptop, cup ...)
    kind: str                  # joined | left | refused (seen long enough, but not on settings → agents.can_join)
    ts: float
    confidence: float          # how sure the detector was
    seen_for_sec: float        # how long it had been seen
    on_desk: bool | None       # inside the marked desk (None = no desk corners marked)
    step: str                  # the sensitivity step in use (settings → agents.sensitivity_steps), e.g. Normal


# Why the sensitivity reviewer moved the slider, or didn't (SensitivityVerdict.why).
SENSITIVITY_WHYS = (
    'off',                  # the Settings page's "Adjust by itself" switch is off
    'waiting',              # you moved the slider lately: it waits (settings → sensitivity_review)
    'not_enough_watching',  # the camera hasn't seen enough since the last change to tell
    'jumpy',                # things flashed on and off: one step pickier
    'missing',              # things stayed in view without counting: one step easier
    'mixed',                # both at once: no change
    'at_the_end',           # it would move, but the slider is already at that end
    'too_soon_to_undo',     # it would move back the way it came, but it moved the slider itself too recently
                            # (settings → sensitivity_review.wait_before_undo_hours)
    'steady',               # nothing to fix
)


@dataclass(frozen=True)
class SensitivityVerdict:
    """One look back by the sensitivity reviewer (desktalk/core/agents/sensitivity_review.py): what it found and
    where it left the slider. Steps are indexes into settings → agents.sensitivity_steps (0 = the pickiest)."""

    ts: float
    from_step: int
    to_step: int                # the same as from_step when it left the slider alone
    why: str                    # one of SENSITIVITY_WHYS
    watched_hours: float        # how long the camera saw something in the stretch it looked at
    blinks: int                 # things put down and taken away again within settings → sensitivity_review.blink_sec
    misses: int                 # things in view long enough that the next easier step would have counted

    @property
    def moved(self) -> bool:
        return self.to_step != self.from_step

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensitivityVerdict:
        """Raises TypeError, KeyError or ValueError for a dict that isn't one (a broken saved file)."""
        verdict = cls(ts=float(data['ts']), from_step=int(data['from_step']), to_step=int(data['to_step']),
                      why=str(data['why']), watched_hours=float(data['watched_hours']), blinks=int(data['blinks']),
                      misses=int(data['misses']))
        if verdict.why not in SENSITIVITY_WHYS:
            raise ValueError(f'not a sensitivity verdict: {verdict.why!r}')
        return verdict
