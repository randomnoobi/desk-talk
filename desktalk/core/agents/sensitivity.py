"""
The Settings page's sensitivity slider (Preferences 'sensitivity'): which of settings → agents.sensitivity_steps
is in use, and how sure a box must be. The roster, the habits and the object states all read it with every
picture, so a change works at once. A new thing (an object without its own block in agents.yaml) needs a surer
box: settings → agents.new_things.
"""

from __future__ import annotations

import logging

from desktalk.config import AgentsSettings, NewThingsSettings, SensitivityStep
from desktalk.domain import AgentProfile
from desktalk.ports import Preferences

log = logging.getLogger(__name__)


class Slider:
    """The slider's step right now."""

    def __init__(self, agents: AgentsSettings, prefs: Preferences):
        self._agents = agents
        self._prefs = prefs
        self._bad: set[str] = set()                    # bad slider values already logged

    def step(self) -> SensitivityStep:
        """The slider's step; agents.sensitivity_default when the saved value is missing or not a step."""
        value = self._prefs.get('sensitivity')
        step = self._agents.step_at(value)
        if step is not None:
            return step
        if value is not None and repr(value) not in self._bad:
            self._bad.add(repr(value))
            log.warning('The sensitivity preference %r is not one of the %d steps: using %s', value,
                        len(self._agents.sensitivity_steps), self._agents.default_step().name)
        return self._agents.default_step()


def confidence_needed(step: SensitivityStep, profile: AgentProfile | None, new_things: NewThingsSettings) -> float:
    """How sure a box must be: the step's min_confidence, or the agent's own in agents.yaml when that is stricter.
    A new thing (an agent made up for an object without a block) needs new_things.extra_confidence more."""
    own = profile.min_confidence if profile is not None else None
    needed = max(step.min_confidence, own or 0)
    return new_things.bar(needed) if profile is not None and profile.auto else needed
