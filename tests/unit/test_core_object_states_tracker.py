"""The tracker: detection events in, steady object states and ObjectStateChanged events out, saved in a store.

Pictures come 3 a second with made-up times. The settings are built here (the numbers from the plan's table),
so changing config/settings.yaml never breaks these tests.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from desktalk.config import ObjectStatesSettings
from desktalk.core.object_states import ObjectStateTracker
from desktalk.domain import BBox, Detection, DetectionEvent, ObjectStateChanged, Phase, StateChange, StateRecord
from desktalk.domain.events import Light
from tests.contracts.object_states_contract import ObjectStatesContract
from tests.fakes import FakeClock, InMemoryStateStore, SyncEventBus

SETTINGS = {
    'on_confidence': 0.5,
    'off_confidence': 0.4,
    'window_pictures': 5,
    'vote_share': 0.5,
    'appear_sec': 3,
    'gone_sec': 3,
    'move_fraction': 0.2,
    'no_move_labels': ['person'],
    'person_label': 'person',
    'person_appear_sec': 3,
    'person_gone_sec': 90,
    'person_counts_movement': True,
    'snapshot_every_sec': 60,
    'restore_max_age_sec': 300,
    'database': 'data/object_states.sqlite',
}
STEP = 1 / 3          # 3 pictures a second
SETTLE = 15           # pictures it takes to settle into a state (5 s)


# ── helpers ──

def picture(ts, *dets, size=(640, 480), moved=False):
    return DetectionEvent(ts=ts, frame_id=f'pi-{ts}', source='pi', size=size, detections=tuple(dets),
                          person=any(d.label == 'person' for d in dets), light=Light(brightness=120), moved=moved)


def o(label, conf, x=320, y=240):
    """A detection with a 40×40 box around (x, y)."""
    return Detection(label, conf, BBox(x - 20, y - 20, x + 20, y + 20))


def make_tracker(store=None, utc_offset_hours=8, **changes):
    bus = SyncEventBus()
    store = InMemoryStateStore() if store is None else store
    settings = ObjectStatesSettings.model_validate({**SETTINGS, **changes})
    tracker = ObjectStateTracker(settings, bus, store, device_id='pi', new_session_after_sec=300,
                                 clock=FakeClock(utc_offset_hours=utc_offset_hours))
    return tracker, bus, store


def feed(tracker, t0, rows, step=STEP, size=(640, 480), moved=False):
    """One picture per row (a list of detections). Returns the time of the next picture."""
    for i, row in enumerate(rows):
        tracker.on_detection(picture(t0 + i * step, *row, size=size, moved=moved))
    return t0 + len(rows) * step


def feed_rows(tracker, t0, rows, label='bottle', step=STEP):
    """Like feed(), but returns the object's state after each row."""
    states = []
    for i, row in enumerate(rows):
        tracker.on_detection(picture(t0 + i * step, *row))
        states.append(tracker.snapshot().objects[label].state)
    return states


def changes(bus):
    return [(e.change, e.label) for e in bus.of_type(ObjectStateChanged)]


def state_of(tracker, label):
    return tracker.snapshot().objects[label].state


def record(ts, label, kind, state='PRESENT'):
    """One row of the state history. A change row gets the states it implies; a snapshot row stays in `state`."""
    prev, new = {
        'appeared': (Phase.ABSENT, Phase.PRESENT),
        'removed': (Phase.PRESENT, Phase.ABSENT),
        'moved': (Phase.PRESENT, Phase.PRESENT),
    }.get(kind, (Phase(state), Phase(state)))
    return StateRecord(ts=ts, timezone='+08:00', device_id='pi', type=kind, label=label, prev_state=prev,
                       new_state=new, dwell_sec=0.0)


class BrokenStore(InMemoryStateStore):
    """A store whose disk is full (until the test sets `full` to False)."""

    def __init__(self):
        super().__init__()
        self.full = True

    def append(self, records):
        if self.full:
            raise sqlite3.OperationalError('database or disk is full')
        super().append(records)


class FlakyStore(InMemoryStateStore):
    """A store whose database file is locked the first `fails` times, then works."""

    def __init__(self, fails=1):
        super().__init__()
        self.fails = fails

    def append(self, records):
        if self.fails:
            self.fails -= 1
            raise sqlite3.OperationalError('database is locked')
        super().append(records)


# ── the whiteboard and smoothing ──

def test_bottle_timeline_from_the_whiteboard():
    tracker, bus, store = make_tracker()
    t = feed(tracker, t0=1000, rows=[[o('bottle', 0.6)]] * SETTLE)     # settle in: PRESENT
    assert changes(bus) == [('appeared', 'bottle')]
    table = [[o('bottle', 0.6)], [o('bottle', 0.5)], [], [o('bottle', 0.55)]] + [[]] * 12
    states = feed_rows(tracker, t, table)          # the bottle's state after each row
    assert all(s == 'PRESENT' for s in states[:14])                 # pictures 1–14
    assert states[15] == 'ABSENT'                                   # picture 16
    assert changes(bus) == [('appeared', 'bottle'), ('removed', 'bottle')]


def test_wobbling_confidence_gives_one_appeared():
    tracker, bus, _ = make_tracker()
    wobble = [0.52, 0.48, 0.51, 0.47, 0.53]
    feed(tracker, 1000, [[o('bottle', wobble[i % len(wobble)])] for i in range(30)])
    assert changes(bus) == [('appeared', 'bottle')]


def test_it_listens_on_the_bus():
    tracker, bus, _ = make_tracker()
    for i in range(SETTLE):
        bus.publish(picture(1000 + i * STEP, o('bottle', 0.6)))
    assert state_of(tracker, 'bottle') == 'PRESENT'
    assert changes(bus) == [('appeared', 'bottle')]


# ── the bus and the store ──

def test_events_go_to_the_bus_and_the_store():
    tracker, bus, store = make_tracker()
    feed(tracker, 1000, [[o('bottle', 0.6)]] * SETTLE)
    [event] = bus.of_type(ObjectStateChanged)
    assert event.device_id == 'pi'
    assert (event.change, event.label) == ('appeared', 'bottle')
    assert (event.prev_state, event.new_state) == ('UNKNOWN', 'PRESENT')
    assert event.conf_avg == pytest.approx(0.6)
    assert event.bbox == (300.0, 220.0, 340.0, 260.0)
    [row] = store.changes(0)
    assert row.new_state == Phase.PRESENT
    assert row.prev_state == Phase.UNKNOWN
    assert (row.type, row.label, row.device_id, row.ts) == ('appeared', 'bottle', 'pi', event.ts)
    assert row.timezone == '+08:00'          # the clock's UTC offset


def test_boxes_are_rounded_like_detections():
    """A box keeps 0.1 px, like Detection.to_dict() and the rows saved before."""
    tracker, bus, store = make_tracker()
    feed(tracker, 1000, [[Detection('bottle', 0.6, BBox(300.04, 219.96, 340.04, 259.96))]] * SETTLE)
    [event] = bus.of_type(ObjectStateChanged)
    assert event.bbox == (300.0, 220.0, 340.0, 260.0)
    [row] = store.changes(0)
    assert row.bbox == (300.0, 220.0, 340.0, 260.0)


@pytest.mark.parametrize('hours, text', [(0, '+00:00'), (-7, '-07:00'), (5.75, '+05:45'), (-3.5, '-03:30')])
def test_each_row_says_the_clocks_utc_offset(hours, text):
    tracker, _, store = make_tracker(utc_offset_hours=hours)
    feed(tracker, 1000, [[o('bottle', 0.6)]] * SETTLE)
    [row] = store.changes(0)
    assert row.timezone == text


def test_a_failing_store_does_not_hide_the_event(caplog):
    tracker, bus, _ = make_tracker(BrokenStore())
    with caplog.at_level(logging.ERROR):
        t = feed(tracker, 1000, [[o('bottle', 0.6)]] * SETTLE)
        assert changes(bus) == [('appeared', 'bottle')]
        assert 'disk is full' in caplog.text
        feed(tracker, t, [[]] * 16)                                  # the next pictures still change states
    assert changes(bus) == [('appeared', 'bottle'), ('removed', 'bottle')]
    assert state_of(tracker, 'bottle') == 'ABSENT'
    errors = [r for r in caplog.records if 'disk is full' in r.getMessage()]
    assert [bool(r.exc_info) for r in errors] == [True, False]     # the traceback only the first time


def test_the_store_working_again_brings_back_the_traceback(caplog):
    store = BrokenStore()
    tracker, bus, _ = make_tracker(store)
    with caplog.at_level(logging.ERROR):
        t = feed(tracker, 1000, [[o('bottle', 0.6)]] * SETTLE)      # fails: traceback
        store.full = False                                            # works again
        t = feed(tracker, t, [[]] * 16)                               # removed is saved
        store.full = True                                             # fails again: traceback again
        feed(tracker, t, [[o('bottle', 0.6)]] * SETTLE)
    errors = [r for r in caplog.records if 'disk is full' in r.getMessage()]
    assert [bool(r.exc_info) for r in errors] == [True, True]


def test_changes_include_ones_the_store_could_not_save():
    store = FlakyStore(fails=1)
    tracker, bus, _ = make_tracker(store)
    t = feed(tracker, 1000, [[o('bottle', 0.6)]] * SETTLE)          # appeared, but the store fails
    [appeared] = bus.of_type(ObjectStateChanged)
    assert store.records == []
    assert tracker.changes(0) == [StateChange(appeared.ts, 'bottle', 'appeared')]
    assert tracker.changes(appeared.ts) == []
    feed(tracker, t, [[]] * 16)                                      # removed: the store works again
    removed = bus.of_type(ObjectStateChanged)[-1]
    assert [(r.type, r.ts) for r in store.records] == [('appeared', appeared.ts), ('removed', removed.ts)]
    assert tracker.changes(0) == [StateChange(appeared.ts, 'bottle', 'appeared'),
                                  StateChange(removed.ts, 'bottle', 'removed')]


# ── snapshots and restarts ──

def test_snapshot_writes_one_row_per_known_object():
    tracker, _, store = make_tracker()
    t = feed(tracker, 1000, [[o('bottle', 0.6, x=100), o('cup', 0.6, x=500)]] * SETTLE)
    t = feed(tracker, t, [[o('bottle', 0.6, x=100)]] * 16)                 # the cup goes: ABSENT
    tracker.on_detection(picture(t, o('bottle', 0.6, x=100), o('book', 0.9, x=320)))    # the book: UNKNOWN
    snap = tracker.snapshot().objects
    assert {k: v.state for k, v in snap.items()} == {'bottle': 'PRESENT', 'cup': 'ABSENT', 'book': 'UNKNOWN'}
    now = t + 10
    assert tracker.save_snapshot(now) == 1                 # only PRESENT: the cup's `removed` row already says it
    rows = {r.label: r for r in store.records if r.type == 'snapshot'}
    assert set(rows) == {'bottle'}
    for label, row in rows.items():
        assert row.ts == now
        assert row.prev_state == row.new_state == Phase(snap[label].state)
        since = snap[label].since
        assert since is not None
        assert row.dwell_sec == pytest.approx(now - since)
        assert row.device_id == 'pi'
    assert store.changes(0) == [r for r in store.records if r.type != 'snapshot']     # snapshots aren't changes


def test_snapshot_lists_unknown_while_deciding():
    tracker, _, _ = make_tracker()
    tracker.on_detection(picture(1000, o('bottle', 0.6)))
    snap = tracker.snapshot()
    assert snap.objects['bottle'].state == 'UNKNOWN'
    assert 'person' not in snap.objects


def test_restart_restores_states_without_new_events():
    first, _, store = make_tracker()
    t = feed(first, 1000, [[o('bottle', 0.6)]] * SETTLE)
    since = first.snapshot().objects['bottle'].since
    assert first.save_snapshot(t) == 1

    second, bus, _ = make_tracker(store)
    assert second.restore(now=t + 30) == 1
    restored = second.snapshot().objects['bottle']
    assert restored.state == 'PRESENT'
    assert restored.since == pytest.approx(since)
    feed(second, t + 30, [[o('bottle', 0.6)]] * SETTLE)
    assert changes(bus) == []


def test_restore_uses_the_time_of_a_change_row():
    store = InMemoryStateStore()
    store.append([record(1000, 'cup', 'removed')])
    tracker, _, _ = make_tracker(store)
    assert tracker.restore(now=1100) == 1
    cup = tracker.snapshot().objects['cup']
    assert (cup.state, cup.since) == ('ABSENT', 1000)


def test_restore_ignores_old_rows():
    store = InMemoryStateStore()
    store.append([record(1000, 'bottle', 'snapshot', 'PRESENT')])
    assert make_tracker(store)[0].restore(now=1301) == 0
    assert make_tracker(store)[0].restore(now=1300) == 1


def test_changes_survive_a_restart():
    first, bus, store = make_tracker()
    feed(first, 1000, [[o('bottle', 0.6)]] * SETTLE)
    [appeared] = bus.of_type(ObjectStateChanged)
    second, _, _ = make_tracker(store)
    assert second.changes(0) == [StateChange(appeared.ts, 'bottle', 'appeared')]


def test_a_restored_object_that_is_gone_gets_removed():
    store = InMemoryStateStore()
    store.append([record(1000, 'bottle', 'snapshot', 'PRESENT')])
    tracker, bus, _ = make_tracker(store)
    tracker.restore(now=1010)
    feed(tracker, 1010, [[]] * SETTLE)                                 # pictures without the bottle
    assert changes(bus) == [('removed', 'bottle')]


# ── the camera's run of pictures ──

def test_camera_gap_does_not_remove_by_itself():
    tracker, bus, _ = make_tracker()
    feed(tracker, 1000, [[o('bottle', 0.6)]] * SETTLE)
    tracker.on_detection(picture(1300))
    assert state_of(tracker, 'bottle') == 'PRESENT'
    feed(tracker, 1300 + STEP, [[]] * SETTLE)
    assert changes(bus) == [('appeared', 'bottle'), ('removed', 'bottle')]
    removed = bus.of_type(ObjectStateChanged)[-1]
    assert 1300 < removed.ts <= 1304.5


def test_watching_starts_again_after_a_long_gap():
    tracker, _, _ = make_tracker()
    assert tracker.snapshot().watched_since is None
    tracker.on_detection(picture(1000))
    assert tracker.snapshot().watched_since == 1000
    tracker.on_detection(picture(1200))
    assert tracker.snapshot().watched_since == 1000
    tracker.on_detection(picture(1600))                                # gap 400 ≥ 300
    assert tracker.snapshot().watched_since == 1600


# ── moved ──

def test_same_spot_in_a_bigger_picture_is_not_moved():
    tracker, bus, _ = make_tracker()
    t = feed(tracker, 1000, [[o('bottle', 0.6, x=320, y=240)]] * SETTLE, size=(640, 480))
    feed(tracker, t, [[o('bottle', 0.6, x=640, y=360)]] * SETTLE, size=(1280, 720))
    assert changes(bus) == [('appeared', 'bottle')]


def test_two_bottles_do_not_fire_moved():
    tracker, bus, _ = make_tracker()
    t = feed(tracker, 1000, [[o('bottle', 0.6, x=100)]] * SETTLE)
    assert state_of(tracker, 'bottle') == 'PRESENT'
    feed(tracker, t, [[o('bottle', 0.6, x=100), o('bottle', 0.9, x=540)]] * 30)
    assert changes(bus) == [('appeared', 'bottle')]


def test_bottle_moved_across_the_desk():
    tracker, bus, _ = make_tracker()
    t = feed(tracker, 1000, [[o('bottle', 0.6, x=100)]] * SETTLE)
    feed(tracker, t, [[o('bottle', 0.6, x=540)]] * 10)
    assert changes(bus) == [('appeared', 'bottle'), ('moved', 'bottle')]
    moved = bus.of_type(ObjectStateChanged)[-1]
    assert (moved.prev_state, moved.new_state) == ('PRESENT', 'PRESENT')


def test_a_faint_box_near_the_old_spot_does_not_hide_the_moved_bottle():
    tracker, bus, _ = make_tracker()
    t = feed(tracker, 1000, [[o('bottle', 0.6, x=128)]] * SETTLE)               # at 0.2 across
    feed(tracker, t, [[o('bottle', 0.9, x=512), o('bottle', 0.25, x=141)]] * 30)  # moved to 0.8; a faint box at 0.22
    assert changes(bus) == [('appeared', 'bottle'), ('moved', 'bottle')]


def test_the_most_confident_box_counts_before_there_is_a_spot():
    tracker, bus, _ = make_tracker()
    feed(tracker, 1000, [[o('bottle', 0.3, x=100), o('bottle', 0.9, x=540)]] * SETTLE)
    assert changes(bus) == [('appeared', 'bottle')]
    assert bus.of_type(ObjectStateChanged)[0].bbox == (520.0, 220.0, 560.0, 260.0)


# ── the person ──

def test_hands_only_keep_the_person_present():
    tracker, bus, _ = make_tracker()
    t = feed(tracker, 1000, [[o('person', 0.8)]] * SETTLE)
    assert state_of(tracker, 'person') == 'PRESENT'
    feed(tracker, t, [[]] * (120 * 3), moved=True)                   # 2 minutes of hands, no person box
    assert state_of(tracker, 'person') == 'PRESENT'
    assert changes(bus) == [('appeared', 'person')]


def test_movement_alone_starts_tracking_the_person():
    tracker, bus, _ = make_tracker()
    feed(tracker, 1000, [[]] * SETTLE, moved=True)
    assert state_of(tracker, 'person') == 'PRESENT'
    assert changes(bus) == [('appeared', 'person')]


def test_person_leaves_after_person_gone_sec():
    tracker, bus, _ = make_tracker()
    gone = SETTINGS['person_gone_sec']
    t = feed(tracker, 1000, [[o('person', 0.8)]] * SETTLE)
    t = feed(tracker, t, [[]] * ((gone - 30) * 3))
    assert state_of(tracker, 'person') == 'PRESENT'
    feed(tracker, t, [[]] * (32 * 3))                                  # now at person_gone_sec + 2
    assert state_of(tracker, 'person') == 'ABSENT'
    assert changes(bus) == [('appeared', 'person'), ('removed', 'person')]


def test_the_person_never_fires_moved():
    tracker, bus, _ = make_tracker()
    t = feed(tracker, 1000, [[o('person', 0.8, x=100)]] * SETTLE)
    feed(tracker, t, [[o('person', 0.8, x=540)]] * SETTLE)
    assert changes(bus) == [('appeared', 'person')]


def test_movement_can_be_switched_off():
    tracker, bus, _ = make_tracker(person_counts_movement=False)
    gone = SETTINGS['person_gone_sec']
    t = feed(tracker, 1000, [[o('person', 0.8)]] * SETTLE)
    feed(tracker, t, [[]] * ((gone + 2) * 3), moved=True)              # hands only
    assert state_of(tracker, 'person') == 'ABSENT'
    assert changes(bus) == [('appeared', 'person'), ('removed', 'person')]


def test_every_number_comes_from_the_settings():
    tracker, bus, _ = make_tracker(appear_sec=1, gone_sec=10, window_pictures=3)
    t = feed(tracker, 1000, [[o('bottle', 0.6)]] * 6)
    [appeared] = bus.of_type(ObjectStateChanged)
    assert appeared.ts < 1002                                          # about 1 s, not 3
    feed(tracker, t, [[]] * (8 * 3))                                   # gone for 8 s
    assert state_of(tracker, 'bottle') == 'PRESENT'
    assert changes(bus) == [('appeared', 'bottle')]


# ── what the rule engine reads ──

class TestObjectStateTracker(ObjectStatesContract):
    @pytest.fixture
    def store(self):
        return InMemoryStateStore()

    @pytest.fixture
    def states(self, store):
        return make_tracker(store)[0]

    @pytest.fixture
    def add_change(self, store):
        return lambda states, ts, label, kind: store.append([record(ts, label, kind)])

    @pytest.fixture
    def put_object(self, store):
        def put(states, label, state, since):
            if state == 'UNKNOWN':
                states.on_detection(picture(since, o(label, 0.9)))        # seen once: not decided yet
            else:
                store.append([record(since, label, 'snapshot', state)])
                states.restore(now=since + 1)
        return put
