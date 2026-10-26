"""
The Settings page's sensitivity slider (Preferences 'sensitivity'): which of settings → agents.sensitivity_steps
is in use, and how sure a box must be. The roster and the habits both read it with every picture, so a change
works at once.
"""

from __future__ import annotations

import logging

from desktalk.config import AgentsSettings, SensitivityStep
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
        steps = self._agents.sensitivity_steps
        value = self._prefs.get('sensitivity')
        if value is None:
            return self._agents.default_step()
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(steps):
            return steps[value]
        if repr(value) not in self._bad:
            self._bad.add(repr(value))
            log.warning('The sensitivity preference %r is not one of the %d steps: using %s', value, len(steps),
                        self._agents.default_step().name)
        return self._agents.default_step()


def confidence_needed(step: SensitivityStep, profile: AgentProfile | None) -> float:
    """How sure a box must be: the step's min_confidence, or the agent's own in agents.yaml when that is stricter."""
    own = profile.min_confidence if profile is not None else None
    return max(step.min_confidence, own or 0)
