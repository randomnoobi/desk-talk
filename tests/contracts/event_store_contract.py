"""What every EventStore must do (desktalk.ports.EventStore)."""

from __future__ import annotations

import time

from desktalk.domain import AgentJoined, Alert

TS = 1_760_000_000.0
DAY = time.strftime('%Y-%m-%d', time.localtime(TS))


class EventStoreContract:
    """Subclass and give a `store` fixture (empty)."""

    def test_written_events_come_back_as_dicts_newest_first(self, store):
        a = AgentJoined(ts=TS, agent='cup', name='Cup', label='cup', conf=0.9)
        b = Alert(ts=TS + 5, kind='proximity', agent='cup')
        store.write(a)
        store.write(b)
        assert store.days() == [DAY]
        assert store.events(DAY, None, 10) == [b.to_dict(), a.to_dict()]

    def test_filter_by_type_and_limit(self, store):
        for i in range(5):
            store.write(AgentJoined(ts=TS + i, agent='cup', name='Cup', label='cup', conf=0.9))
        store.write(Alert(ts=TS + 9, kind='proximity'))
        assert [e['type'] for e in store.events(DAY, {'alert'}, 10)] == ['alert']
        assert len(store.events(DAY, None, 3)) == 3

    def test_unknown_day_is_empty(self, store):
        assert store.events('1999-01-01', None, 10) == []
        assert store.presence('1999-01-01') == []
