"""What every EventBus must do (desktalk.ports.EventBus)."""

from __future__ import annotations

from desktalk.domain import AgentJoined, Alert, HabitEvent


def joined(n=0):
    return AgentJoined(ts=1.0 + n, agent='cup', name='Cup', label='cup', conf=0.9)


class EventBusContract:
    """Subclass and give a `bus` fixture."""

    def test_subscribers_get_only_the_types_they_asked_for(self, bus):
        alerts, everything = [], []
        bus.subscribe('alerts', alerts.append, types={Alert})
        bus.subscribe('all', everything.append)
        alert = Alert(ts=1.0, kind='proximity', agent='cup')
        bus.publish(joined())
        bus.publish(alert)
        assert bus.wait_idle(timeout=2)
        assert alerts == [alert]
        assert len(everything) == 2

    def test_events_arrive_in_order(self, bus):
        got = []
        bus.subscribe('log', got.append)
        events = [joined(i) for i in range(20)]
        for e in events:
            bus.publish(e)
        assert bus.wait_idle(timeout=2)
        assert got == events

    def test_a_failing_handler_is_counted_and_others_keep_going(self, bus):
        got = []

        def broken(_event):
            raise RuntimeError('boom')

        bus.subscribe('broken', broken)
        bus.subscribe('fine', got.append)
        bus.publish(joined(1))
        bus.publish(HabitEvent(ts=2.0, habit='hydration', action='sip'))
        assert bus.wait_idle(timeout=2)
        assert len(got) == 2
        stats = {s['name']: s for s in bus.stats()}
        assert stats['broken']['errors'] == 2
        assert stats['fine']['handled'] == 2
        assert set(stats['fine']) >= {'name', 'handled', 'waiting', 'dropped', 'errors'}
