"""
The sensitivity slider moving by itself. STUB: the real reviewer replaces this (see the plan).
"""

from __future__ import annotations

from desktalk.config import AgentsSettings, SensitivityReviewSettings
from desktalk.domain import SensitivityVerdict
from desktalk.ports import Clock, KeyValueStore, ObjectStateStore, Preferences, PresenceHistory, Profiles

MINUTE = 60


class SensitivityReviewer:
    """Implements SensitivityReview (desktalk/ports.py)."""

    name = 'sensitivity review'

    def __init__(self, settings: SensitivityReviewSettings, agents: AgentsSettings, prefs: Preferences,
                 history: ObjectStateStore, presence: PresenceHistory, store: KeyValueStore, profiles: Profiles,
                 clock: Clock, *, person_label: str):
        self._settings = settings

    def tick(self, now: float) -> float:
        return self._settings.check_every_min * MINUTE

    def last(self) -> SensitivityVerdict | None:
        return None

    def moves(self) -> list[SensitivityVerdict]:
        return []
