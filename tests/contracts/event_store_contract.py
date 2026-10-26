"""What every EventStore must do (desktalk.ports.EventStore)."""

from __future__ import annotations

import time
from pathlib import Path

from desktalk.domain import AgentJoined, Alert

TS = 1_760_000_000.0


class EventStoreContract:
    """Subclass and give a `store` fixture (empty). Override day_of() when the store's clock isn't the computer's."""

    def day_of(self, ts: float) -> str:
        """The day name the store files `ts` under."""
        return time.strftime('%Y-%m-%d', time.localtime(ts))

    def test_written_events_come_back_as_dicts_newest_first(self, store):
        a = AgentJoined(ts=TS, agent='cup', name='Cup', label='cup', conf=0.9)
        b = Alert(ts=TS + 5, kind='proximity', agent='cup')
        store.write(a)
        store.write(b)
        day = self.day_of(TS)
        assert store.days() == [day]
        assert store.events(day, None, 10) == [b.to_dict(), a.to_dict()]

    def test_filter_by_type_and_limit(self, store):
        for i in range(5):
            store.write(AgentJoined(ts=TS + i, agent='cup', name='Cup', label='cup', conf=0.9))
        store.write(Alert(ts=TS + 9, kind='proximity'))
        day = self.day_of(TS)
        assert [e['type'] for e in store.events(day, {'alert'}, 10)] == ['alert']
        assert len(store.events(day, None, 3)) == 3

    def test_unknown_day_is_empty(self, store):
        assert store.events('1999-01-01', None, 10) == []
        assert store.counts('1999-01-01')['pictures_waiting'] == 0

    def test_where_it_is_kept_and_the_last_compaction(self, store):
        assert isinstance(store.folder, Path)
        assert store.last_compaction is None
        store.write(AgentJoined(ts=TS, agent='cup', name='Cup', label='cup', conf=0.9))
        store.compact(TS + 60)
        assert store.last_compaction is not None and store.last_compaction['ts'] == TS + 60
