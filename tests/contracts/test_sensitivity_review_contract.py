"""The sensitivity reviewer passes the SensitivityReview contract."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.sensitivity_review import SensitivityReviewer
from desktalk.domain import AgentProfile
from desktalk.domain.object_states import Phase, StateRecord
from tests.contracts.sensitivity_review_contract import TS, SensitivityReviewContract
from tests.fakes import (
    FakeClock,
    FakePreferences,
    FakeProfiles,
    InMemoryEventStore,
    InMemoryKeyValueStore,
    InMemoryStateStore,
)

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
HOUR = 3600
KEPT = 2
EAGER = len(CONFIG.agents.sensitivity_steps) - 1
# The fake event log files intervals under this computer's local day, so the fake clock uses the same offset.
LOCAL_OFFSET_HOURS = time.localtime(TS).tm_gmtoff / HOUR


class TestSensitivityReviewer(SensitivityReviewContract):
    changes_kept = KEPT

    @pytest.fixture
    def review(self):
        self.clock = FakeClock(TS, utc_offset_hours=LOCAL_OFFSET_HOURS)
        self.history = InMemoryStateStore()
        self.log = InMemoryEventStore()
        settings = CONFIG.sensitivity_review.model_copy(update={
            'changes_kept': KEPT, 'min_watch_hours': 2, 'blink_sec': 15, 'jumpy_per_hour': 6})
        return SensitivityReviewer(settings, CONFIG.agents, CONFIG.object_states,
                                   FakePreferences({'sensitivity': EAGER, 'auto_sensitivity': True}), self.history,
                                   self.log, self.log, InMemoryKeyValueStore(),
                                   FakeProfiles([AgentProfile(id='cup', name='Cup', labels=['cup'])]), self.clock,
                                   person_label='person')

    def make_it_move(self, review, times):
        """Things flashing on and off: one step pickier each look."""
        assert times < len(CONFIG.agents.sensitivity_steps)
        review.tick(self.clock.now())                              # the first look starts fresh
        for _ in range(times):
            start = self.clock.now()
            self.clock.advance(3 * HOUR)
            self.log.add_presence('person', start, start + 3 * HOUR, avg_conf=0.2)
            self.history.append([StateRecord(ts=start + 60 * (i + 1), timezone='+00:00', device_id='pi',
                                             type='removed', label='cup', prev_state=Phase.PRESENT,
                                             new_state=Phase.ABSENT, dwell_sec=1.0) for i in range(30)])
            review.tick(self.clock.now())
