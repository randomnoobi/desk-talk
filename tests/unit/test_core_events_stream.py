"""The event stream: producers publish typed events, each consumer gets them on its own queue and thread."""

from __future__ import annotations

import logging
import threading

import pytest

from desktalk.config import EventStreamSettings
from desktalk.core.events import EventStream
from desktalk.domain import AgentJoined, Alert, DetectionEvent, Event, HabitEvent, Light, NudgeSent
from tests.contracts.event_bus_contract import EventBusContract

WAIT = 2.0          # longest a test waits for a thread; tests finish long before unless something is broken


def stream(queue_size: int = 100, **kw) -> EventStream:
    return EventStream(EventStreamSettings(queue_size=queue_size), **kw)


@pytest.fixture
def threaded():
    """Makes threaded streams and closes them after the test."""
    made = []

    def make(queue_size: int = 100) -> EventStream:
        made.append(stream(queue_size))
        return made[-1]

    yield make
    for bus in made:
        bus.close(timeout=WAIT)


def detection(n: int) -> DetectionEvent:
    return DetectionEvent(ts=float(n), frame_id=f'pi-{n}', source='pi', size=(640, 480), detections=(),
                          person=False, light=Light(brightness=120.0))


def joined(n: int) -> AgentJoined:
    return AgentJoined(ts=float(n), agent='cup', name='Cup', label='cup', conf=0.9)


def broken(_event):
    raise RuntimeError('boom')


def stats_of(bus: EventStream, name: str) -> dict:
    return next(s for s in bus.stats() if s['name'] == name)


class Blocked:
    """A consumer stuck on its first event until the test lets it go, so its queue fills up."""

    def __init__(self, bus: EventStream, first: Event):
        self.first = first
        self.got: list[Event] = []
        self._started, self._release = threading.Event(), threading.Event()
        bus.subscribe('blocked', self._handle)
        bus.publish(first)
        assert self._started.wait(WAIT)

    def _handle(self, event: Event) -> None:
        self._started.set()
        self._release.wait(WAIT)
        self.got.append(event)

    def release(self) -> None:
        self._release.set()


# ── The EventBus contract, both ways ─────────────────────────────────
class TestSynchronousEventStream(EventBusContract):
    @pytest.fixture
    def bus(self):
        return stream(synchronous=True)


class TestThreadedEventStream(EventBusContract):
    @pytest.fixture
    def bus(self, threaded):
        return threaded()


# ── Synchronous mode ─────────────────────────────────────────────────
def test_handlers_run_inside_publish_in_the_order_they_subscribed():
    bus = stream(synchronous=True)
    got = []
    bus.subscribe('all', lambda e: got.append(('all', e.TYPE)))
    bus.subscribe('pictures', lambda e: got.append(('pictures', e.TYPE)), types={DetectionEvent})
    bus.publish(detection(1))
    bus.publish(Alert(ts=2.0, kind='proximity'))
    assert got == [('all', 'detection'), ('pictures', 'detection'), ('all', 'alert')]


# ── Handler errors ───────────────────────────────────────────────────
def test_a_failing_handler_is_logged_with_its_name_and_the_event(caplog):
    bus = stream(synchronous=True)
    bus.subscribe('roster', broken)
    with caplog.at_level(logging.ERROR):
        bus.publish(detection(1))
    assert len(caplog.records) == 1
    assert 'roster' in caplog.text and 'detection' in caplog.text and 'RuntimeError: boom' in caplog.text


def test_only_the_first_failure_in_a_row_logs_the_full_traceback(caplog):
    bus = stream(synchronous=True)

    def fails_on_odd(event):
        if int(event.ts) % 2:
            raise RuntimeError('boom')

    bus.subscribe('roster', fails_on_odd)
    with caplog.at_level(logging.ERROR):
        for n in (1, 3, 4, 5):            # fail, fail, works, fail
            bus.publish(detection(n))
    assert [bool(r.exc_info) for r in caplog.records] == [True, False, True]


# ── Slow consumers ───────────────────────────────────────────────────
def test_a_slow_consumer_holds_up_neither_the_publisher_nor_other_consumers(threaded):
    bus = threaded()
    release, slow_done, fast_got_all = threading.Event(), threading.Event(), threading.Event()

    def slow(_event):
        release.wait(WAIT)
        slow_done.set()

    bus.subscribe('slow', slow)
    bus.subscribe('fast', lambda e: e.ts == 3 and fast_got_all.set())
    for n in (1, 2, 3):
        bus.publish(detection(n))
    assert not slow_done.is_set()                 # publish() came back while the slow one was still busy
    assert fast_got_all.wait(WAIT)
    release.set()
    assert bus.wait_idle(WAIT)


def test_a_full_queue_drops_the_oldest_picture_and_counts_it(threaded):
    bus = threaded(queue_size=3)
    blocked = Blocked(bus, detection(0))
    for n in range(1, 10):
        bus.publish(detection(n))
    assert stats_of(bus, 'blocked')['waiting'] == 3
    blocked.release()
    assert bus.wait_idle(WAIT)
    assert [e.ts for e in blocked.got] == [0, 7, 8, 9]          # the newest pictures get through
    assert stats_of(bus, 'blocked')['dropped'] == 6


def test_a_full_queue_never_drops_joins_alerts_habits_or_nudges(threaded):
    bus = threaded(queue_size=3)
    blocked = Blocked(bus, detection(0))
    alert, join = Alert(ts=1.0, kind='proximity'), joined(5)
    habit = HabitEvent(ts=6.0, habit='hydration', action='sip')
    nudge = NudgeSent(ts=8.0, habit='hydration', rule='sip', agent='cup', evidence='', text='Drink up')
    for event in (alert, detection(2), detection(3), detection(4), join, habit, detection(7), nudge):
        bus.publish(event)
    blocked.release()
    assert bus.wait_idle(WAIT)
    # 4 pushed out 2, the join pushed out 3, the habit pushed out 4; then no picture was left to drop,
    # so picture 7 was dropped itself and the nudge got in anyway.
    assert blocked.got == [blocked.first, alert, join, habit, nudge]
    assert stats_of(bus, 'blocked')['dropped'] == 4


# ── wait_idle and close ──────────────────────────────────────────────
def test_wait_idle_waits_for_busy_consumers_and_the_events_they_publish(threaded):
    bus = threaded()
    release, chat = threading.Event(), []

    def roster(_event):
        release.wait(WAIT)
        bus.publish(joined(1))

    bus.subscribe('roster', roster, types={DetectionEvent})
    bus.subscribe('chat', chat.append, types={AgentJoined})
    bus.publish(detection(0))
    assert bus.wait_idle(timeout=0.05) is False           # the roster is still on the picture
    release.set()
    assert bus.wait_idle(WAIT)
    assert [e.ts for e in chat] == [1.0]


def test_close_stops_every_consumer_thread():
    before = set(threading.enumerate())
    bus = stream()
    bus.subscribe('log', lambda e: None)
    bus.subscribe('roster', lambda e: None)
    assert len(set(threading.enumerate()) - before) == 2
    assert bus.close(timeout=WAIT) == []
    assert set(threading.enumerate()) - before == set()


def test_close_names_the_consumers_still_busy_after_the_wait(threaded):
    bus = threaded()
    blocked = Blocked(bus, detection(0))
    bus.subscribe('idle', lambda e: None)
    assert bus.close(timeout=0.01) == ['blocked']
    blocked.release()


@pytest.mark.parametrize('synchronous', [True, False])
def test_events_published_after_close_are_ignored(synchronous):
    bus = stream(synchronous=synchronous)
    got = []
    bus.subscribe('log', got.append)
    bus.close(timeout=WAIT)
    bus.publish(joined(1))
    assert got == []
    assert stats_of(bus, 'log')['waiting'] == 0
