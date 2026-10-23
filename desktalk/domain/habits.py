"""A habit reminder the Protocols rules (config/protocols.yaml) want to send."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Nudge:
    habit: str                 # hydration | breaks | light | phone_free | screens | food | tasks
    key: str                   # rule id, used for "don't repeat too soon"
    evidence: str              # what the camera saw (the AI must mention it)
    text: str                  # ready-made message (used without AI, and by Lens)
    repeat_after_sec: float    # don't send this rule again sooner than this
    tip: str = ''              # what the book recommends (given to the AI)
    once_per_day: bool = False
    speakers: tuple = field(default_factory=tuple)   # habits whose agents may say it

    def __post_init__(self):
        if not self.speakers:
            self.speakers = (self.habit,)
