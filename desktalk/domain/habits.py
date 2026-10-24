"""A habit reminder the Protocols rules (config/protocols.yaml) want to send."""

from __future__ import annotations

from dataclasses import dataclass, field

# The habits the app really logs, with the plain words the evidence uses for each: "no sip has been logged in
# over 60 minutes". Any log of the habit counts, whatever it was (the app's buttons, the chat or the camera).
# A rule may only name a habit in this list. Screens and tasks are left out because nothing logs them yet.
HABIT_WORDS: dict[str, str] = {
    'hydration': 'sip',
    'breaks': 'break',
    'light': 'sunlight',
    'food': 'food',
    'phone_free': 'phone-free stretch',
}


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
