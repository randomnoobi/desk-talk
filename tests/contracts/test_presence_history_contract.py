"""The event log on this computer and its in-memory fake both pass the PresenceHistory contract."""

from __future__ import annotations

import pytest

from desktalk.adapters.storage import EventLog
from desktalk.config import EventLogSettings
from desktalk.ports import PresenceHistory
from tests.contracts.presence_history_contract import PresenceHistoryContract
from tests.fakes import FakeClock, InMemoryEventStore

CLOCK = FakeClock()                            # the log's day names come from its clock (UTC here)
MERGE_GAP_SEC = 3


class TestEventLogPresence(PresenceHistoryContract):
    merge_gap_sec = MERGE_GAP_SEC

    @pytest.fixture
    def store(self, tmp_path):
        settings = EventLogSettings(folder=tmp_path, compact_every_min=10, merge_gap_sec=MERGE_GAP_SEC,
                                    keep_days=30, log_empty_frames=True, prune_every_min=60)
        return EventLog(tmp_path, settings, CLOCK)

    def day_of(self, ts: float) -> str:
        return CLOCK.local_day(ts)

    def test_is_a_presence_history(self, store):
        assert isinstance(store, PresenceHistory)


class TestInMemoryEventStorePresence(PresenceHistoryContract):
    merge_gap_sec = InMemoryEventStore().merge_gap_sec

    @pytest.fixture
    def store(self):
        return InMemoryEventStore()

    def test_is_a_presence_history(self, store):
        assert isinstance(store, PresenceHistory)
