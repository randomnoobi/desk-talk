"""The sensitivity slider moving by itself: review() judges a stretch of time, SensitivityReviewer runs it."""

import logging
import shutil
import time
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.agents.sensitivity_review import MEMORY_KEY, SensitivityReviewer, days_covered, review
from desktalk.domain import AgentJoined, AgentLeft, AgentProfile, SensitivityVerdict
from desktalk.domain.object_states import Phase, StateRecord
from tests.fakes import (
    FakeClock,
    FakePreferences,
    FakeProfiles,
    InMemoryEventStore,
    InMemoryKeyValueStore,
    InMemoryStateStore,
)

ROOT = Path(__file__).resolve().parents[2]
HOUR = 3600
T0 = 1_760_000_000.0
# The fake event log files intervals under this computer's local day, so the fake clock uses the same offset.
LOCAL_OFFSET_HOURS = time.localtime(T0).tm_gmtoff / HOUR
LOG = 'desktalk.core.agents.sensitivity_review'

# Normal is step 2. A known cup needs 0.45 there and 0.40 at Easy (config/settings.yaml → agents.sensitivity_steps).
NORMAL = 2
BARS = {('cup', 0): 0.65, ('cup', 1): 0.55, ('cup', 2): 0.45, ('cup', 3): 0.40, ('cup', 4): 0.35}


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={})


@pytest.fixture(scope='module')
def settings(config):
    return config.sensitivity_review.model_copy(update={
        'check_every_min': 60, 'look_back_hours': 24, 'min_watch_hours': 2, 'wait_after_you_change_hours': 24,
        'blink_sec': 15, 'jumpy_per_hour': 6, 'missed_min_sec': 60, 'missed_at_least': 3, 'changes_kept': 3,
        'wait_before_undo_hours': 24})


@pytest.fixture(scope='module')
def agents(config):
    return config.agents


@pytest.fixture(scope='module')
def object_states(config):
    return config.object_states.model_copy(update={'gone_sec': 3})


def bar(label, step):
    return BARS.get((label, step))


def removed(ts, label='cup', dwell: float | None = 5.0):
    return StateRecord(ts=ts, timezone='+00:00', device_id='pi', type='removed', label=label,
                       prev_state=Phase.PRESENT, new_state=Phase.ABSENT, dwell_sec=dwell)


def appeared(ts, label='cup', dwell=5.0):
    return StateRecord(ts=ts, timezone='+00:00', device_id='pi', type='appeared', label=label,
                       prev_state=Phase.ABSENT, new_state=Phase.PRESENT, dwell_sec=dwell)


def seen(label, start, end, conf=0.9, center=None, size=None):
    interval = {'label': label, 'interval': [start, end], 'frames': 10, 'avg_conf': conf}
    if center is not None:
        interval['center'] = list(center)
    if size is not None:
        interval['size'] = list(size)
    return interval


def judge(settings, agents, *, changes=(), intervals=(), joins=(), gone_sec=0.0, on_desk=None, step=NORMAL,
          start=T0, now=T0 + 4 * HOUR):
    return review(step=step, steps=agents.sensitivity_steps, settings=settings, changes=list(changes),
                  intervals=list(intervals), joins=list(joins), gone_sec=gone_sec, on_desk=on_desk, bar=bar,
                  person_label='person', start=start, now=now)


def watched(hours, start=T0):
    """The person in view for `hours`: watching, but never a blink or a miss."""
    return [seen('person', start, start + hours * HOUR, conf=0.2)]


# ── review(): the pure judgement ──────────────────────────────────────────────────────────────────────────────

def test_nothing_wrong_is_steady(settings, agents):
    verdict = judge(settings, agents, intervals=watched(3))
    assert verdict.why == 'steady' and not verdict.moved
    assert verdict.watched_hours == pytest.approx(3) and verdict.blinks == 0 and verdict.misses == 0
    assert verdict.ts == T0 + 4 * HOUR and verdict.from_step == verdict.to_step == NORMAL


def test_too_little_watching_is_not_judged(settings, agents):
    blinks = [removed(T0 + i) for i in range(1, 50)]
    verdict = judge(settings, agents, changes=blinks, intervals=watched(1.5))
    assert verdict.why == 'not_enough_watching' and not verdict.moved
    assert judge(settings, agents).why == 'not_enough_watching'      # nothing seen at all


def test_hours_watched_is_the_union_of_every_interval(settings, agents):
    intervals = [seen('person', T0, T0 + HOUR), seen('cup', T0 + 0.5 * HOUR, T0 + 1.5 * HOUR),
                 seen('book', T0 + 1.2 * HOUR, T0 + 1.4 * HOUR), seen('cup', T0 + 2 * HOUR, T0 + 2.5 * HOUR)]
    assert judge(settings, agents, intervals=intervals).watched_hours == pytest.approx(2.0)


def test_intervals_are_clipped_to_the_stretch_looked_at(settings, agents):
    intervals = [seen('person', T0 - 5 * HOUR, T0 + HOUR), seen('person', T0 + 3 * HOUR, T0 + 9 * HOUR)]
    assert judge(settings, agents, intervals=intervals).watched_hours == pytest.approx(2.0)


def test_many_blinks_an_hour_is_jumpy_one_step_pickier(settings, agents):
    blinks = [removed(T0 + 60 * i) for i in range(1, 19)]             # 18 blinks in 3 hours = 6 an hour
    verdict = judge(settings, agents, changes=blinks, intervals=watched(3))
    assert verdict.why == 'jumpy' and verdict.to_step == NORMAL - 1 and verdict.blinks == 18 and verdict.moved


def test_just_under_the_blink_rate_is_steady(settings, agents):
    blinks = [removed(T0 + 60 * i) for i in range(1, 18)]             # 17 in 3 hours
    assert judge(settings, agents, changes=blinks, intervals=watched(3)).why == 'steady'


def test_a_blink_is_on_the_desk_for_less_than_blink_sec(settings, agents):
    rows = [removed(T0 + 60 * i, dwell=settings.blink_sec) for i in range(1, 30)]          # exactly blink_sec
    rows += [removed(T0 + 3000 + i, dwell=None) for i in range(10)]                         # no dwell known
    rows += [removed(T0 + 4000 + i, label='person', dwell=1) for i in range(30)]           # the person
    rows += [appeared(T0 + 5000 + i, dwell=1) for i in range(30)]                           # back after a sip
    verdict = judge(settings, agents, changes=rows, intervals=watched(3))
    assert verdict.blinks == 0 and verdict.why == 'steady'


def test_blinks_outside_the_stretch_dont_count(settings, agents):
    rows = [removed(T0 - 60 * i) for i in range(30)] + [removed(T0 + 5 * HOUR + i) for i in range(30)]
    assert judge(settings, agents, changes=rows, intervals=watched(3)).blinks == 0


def test_things_in_view_but_not_quite_sure_enough_is_missing_one_step_easier(settings, agents):
    misses = [seen('cup', T0 + i * 600, T0 + i * 600 + 60, conf=0.40) for i in range(3)]   # exactly the easier bar
    verdict = judge(settings, agents, intervals=watched(3) + misses)
    assert verdict.why == 'missing' and verdict.to_step == NORMAL + 1 and verdict.misses == 3


def test_what_is_not_a_miss(settings, agents):
    intervals = watched(3)
    intervals += [seen('cup', T0 + 100 * i, T0 + 100 * i + 59.9, conf=0.42) for i in range(5)]        # too short
    intervals += [seen('cup', T0 + 1000 + 100 * i, T0 + 1100 + 100 * i, conf=0.45) for i in range(5)]  # counts now
    intervals += [seen('cup', T0 + 2000 + 100 * i, T0 + 2100 + 100 * i, conf=0.39) for i in range(5)]  # even Easy no
    intervals += [seen('person', T0 + 3000 + 100 * i, T0 + 3100 + 100 * i, conf=0.42) for i in range(5)]
    intervals += [seen('stapler', T0 + 4000 + 100 * i, T0 + 4100 + 100 * i, conf=0.42) for i in range(5)]  # no bar
    intervals += [{'label': 'cup', 'interval': [T0 + 5000, T0 + 5100]}]                                # no avg_conf
    intervals += [{'label': 'cup', 'interval': 'soon', 'avg_conf': 0.42}, {'interval': [T0, T0 + 100]}]  # broken
    verdict = judge(settings, agents, intervals=intervals)
    assert verdict.misses == 0 and verdict.why == 'steady'


def test_a_miss_counts_only_its_part_inside_the_stretch(settings, agents):
    misses = [seen('cup', T0 - 100 + i * 600, T0 + 30 + i * 600, conf=0.42) for i in range(1)]   # 30 s inside
    misses += [seen('cup', T0 + 600 * i, T0 + 600 * i + 60, conf=0.42) for i in range(1, 3)]
    assert judge(settings, agents, intervals=watched(3) + misses).misses == 2


def test_at_the_easiest_step_there_is_nothing_easier_to_miss(settings, agents):
    misses = [seen('cup', T0 + i * 600, T0 + i * 600 + 60, conf=0.30) for i in range(5)]
    verdict = judge(settings, agents, intervals=watched(3) + misses, step=len(agents.sensitivity_steps) - 1)
    assert verdict.misses == 0 and verdict.why == 'steady'


def test_both_at_once_is_mixed(settings, agents):
    blinks = [removed(T0 + 60 * i) for i in range(1, 30)]
    misses = [seen('cup', T0 + i * 600, T0 + i * 600 + 60, conf=0.42) for i in range(3)]
    verdict = judge(settings, agents, changes=blinks, intervals=watched(3) + misses)
    assert verdict.why == 'mixed' and not verdict.moved and verdict.blinks == 29 and verdict.misses == 3


def test_jumpy_at_the_pickiest_step_is_at_the_end(settings, agents):
    blinks = [removed(T0 + 60 * i) for i in range(1, 30)]
    verdict = judge(settings, agents, changes=blinks, intervals=watched(3), step=0)
    assert verdict.why == 'at_the_end' and verdict.to_step == 0 and not verdict.moved


def test_a_blink_leaves_out_the_wait_before_gone_counts(settings, agents):
    gone = 3.0                                    # dwell_sec of a removed row includes the gone wait
    rows = [removed(T0 + 60 * i, dwell=settings.blink_sec + gone - 0.1) for i in range(1, 19)]
    assert judge(settings, agents, changes=rows, intervals=watched(3), gone_sec=gone).blinks == 18
    rows = [removed(T0 + 60 * i, dwell=settings.blink_sec + gone) for i in range(1, 19)]
    assert judge(settings, agents, changes=rows, intervals=watched(3), gone_sec=gone).blinks == 0


def cups(count, conf=0.42, start=T0, **kwargs):
    """`count` cups, one every 10 minutes, each in view 2 minutes."""
    return [seen('cup', start + i * 600, start + i * 600 + 120, conf=conf, **kwargs) for i in range(count)]


def test_a_thing_that_joined_the_chat_is_not_a_miss(settings, agents):
    joined_during = [(T0 + i * 600 + 30, 'cup', True) for i in range(3)]
    assert judge(settings, agents, intervals=watched(3) + cups(3), joins=joined_during).misses == 0
    in_chat_already = [(T0 - 10, 'cup', True)]
    assert judge(settings, agents, intervals=watched(3) + cups(3), joins=in_chat_already).misses == 0
    left_first = [(T0 + 2 * 600 + 200, 'cup', False)]        # it left after the last one: it was in the chat
    assert judge(settings, agents, intervals=watched(3) + cups(3), joins=left_first).misses == 0


def test_a_thing_that_left_the_chat_before_can_be_a_miss(settings, agents):
    joins = [(T0 - 100, 'cup', True), (T0 - 10, 'cup', False),            # in and out before
             (T0 + 200, 'cup', True), (T0 + 300, 'cup', False),           # between the first and the second
             (T0 + 3 * 600, 'book', True)]                                 # another word
    verdict = judge(settings, agents, intervals=watched(3) + cups(3), joins=joins)
    assert verdict.misses == 3 and verdict.why == 'missing'


def test_off_the_desk_is_not_a_miss(settings, agents):
    def on_desk(x, y, width, height):
        assert (width, height) == (640, 480)
        return x < 320

    off = cups(3, center=(500, 100), size=(640, 480))
    on = cups(3, start=T0 + HOUR, center=(100, 100), size=(640, 480))
    assert judge(settings, agents, intervals=watched(3) + off, on_desk=on_desk).misses == 0
    assert judge(settings, agents, intervals=watched(3) + off + on, on_desk=on_desk).misses == 3
    assert judge(settings, agents, intervals=watched(3) + off).misses == 3              # no desk check
    assert judge(settings, agents, intervals=watched(3) + off, on_desk=lambda *a: None).misses == 3   # no desk
    old = cups(3, center=(500, 100))                                       # saved before intervals had a size
    assert judge(settings, agents, intervals=watched(3) + old, on_desk=on_desk).misses == 3
    broken = cups(3, center='middle', size=(640, 480))
    assert judge(settings, agents, intervals=watched(3) + broken, on_desk=on_desk).misses == 3


def test_days_covered_steps_half_a_day_so_a_short_day_is_never_skipped():
    # Made-up local days: A is 24 hours, B only 23 (the clocks went forward), C 24.
    starts = [('A', 0.0), ('B', 24 * HOUR), ('C', 47 * HOUR), ('D', 71 * HOUR)]

    def local_day(t):
        return [name for name, begins in starts if begins <= t][-1]

    assert days_covered(23.5 * HOUR, 50 * HOUR, local_day) == ['A', 'B', 'C']
    assert days_covered(5 * HOUR, 5 * HOUR, local_day) == ['A']
    assert days_covered(5 * HOUR, 72 * HOUR, local_day) == ['A', 'B', 'C', 'D']


# ── SensitivityReviewer: the background job ───────────────────────────────────────────────────────────────────

class Desk:
    """A reviewer with fakes all around it."""

    def __init__(self, settings, agents, object_states, prefs=None, store=None, *, profiles=None, on_desk=None):
        self.clock = FakeClock(T0, utc_offset_hours=LOCAL_OFFSET_HOURS)
        self.prefs = FakePreferences({'sensitivity': NORMAL, 'auto_sensitivity': True} if prefs is None else prefs)
        self.history = InMemoryStateStore()
        self.log = InMemoryEventStore()
        self.store = store if store is not None else InMemoryKeyValueStore()
        self.profiles = profiles or FakeProfiles([AgentProfile(id='cup', name='Cup', labels=['cup']),
                                                  AgentProfile(id='stapler', name='Stapler', labels=['stapler'],
                                                               auto=True)])
        self.settings, self.agents = settings, agents
        self.object_states = object_states
        self.on_desk = on_desk
        self.reviewer = self.make()

    def make(self):
        return SensitivityReviewer(self.settings, self.agents, self.object_states, self.prefs, self.history,
                                   self.log, self.log, self.store, self.profiles, self.clock, person_label='person',
                                   on_desk=self.on_desk)

    def tick(self):
        return self.reviewer.tick(self.clock.now())

    def watch(self, start, hours):
        self.log.add_presence('person', start, start + hours * HOUR, avg_conf=0.2)

    def blinks(self, start, count):
        self.history.append([removed(start + 60 * i) for i in range(count)])

    def misses(self, start, count, label='cup', conf=0.42, size=None):
        for i in range(count):
            self.log.add_presence(label, start + i * 600, start + i * 600 + 120, avg_conf=conf, size=size)

    def look_first(self):
        """Its first look: it starts fresh, judging only what comes after."""
        self.tick()


def test_it_waits_check_every_min_between_looks(settings, agents, object_states):
    assert Desk(settings, agents, object_states).tick() == settings.check_every_min * 60


def test_the_first_look_starts_fresh(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.watch(T0 - 4 * HOUR, 4)                       # before the first look, maybe at another step
    desk.blinks(T0 - 4 * HOUR, 40)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'not_enough_watching' and last.blinks == 0
    assert desk.prefs.values['sensitivity'] == NORMAL
    assert desk.store.data[MEMORY_KEY]['since'] == desk.clock.now()


def test_jumpy_moves_one_step_pickier(settings, agents, object_states, caplog):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.blinks(T0 + HOUR, 20)
    with caplog.at_level(logging.INFO, logger=LOG):
        desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'jumpy' and last.to_step == NORMAL - 1
    assert desk.prefs.values['sensitivity'] == NORMAL - 1
    assert desk.reviewer.moves() == [last]
    assert any('Careful' in r.getMessage() for r in caplog.records)
    saved = desk.store.data[MEMORY_KEY]
    assert saved['step'] == NORMAL - 1 and saved['since'] == desk.clock.now() and saved['hold_until'] is None
    assert saved['last'] == last.to_dict() and saved['moves'] == [last.to_dict()]


def test_it_reads_only_look_back_hours(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(30 * HOUR)
    desk.watch(T0, 4)                                  # more than look_back_hours ago
    desk.blinks(T0, 40)
    last = (desk.tick(), desk.reviewer.last())[1]
    assert last is not None and last.why == 'not_enough_watching' and last.blinks == 0


def test_it_reads_every_day_the_stretch_covers(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(24 * HOUR)
    desk.watch(T0 + HOUR, 1.5)                         # yesterday, for a computer in most time zones
    desk.watch(T0 + 22 * HOUR, 1.5)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.watched_hours == pytest.approx(3)


def test_after_its_own_move_it_judges_only_what_came_after(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.blinks(T0 + HOUR, 20)
    desk.tick()
    desk.clock.advance(HOUR)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'not_enough_watching' and last.from_step == NORMAL - 1
    assert desk.prefs.values['sensitivity'] == NORMAL - 1 and len(desk.reviewer.moves()) == 1


def test_missing_things_move_it_one_step_easier(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.misses(T0 + HOUR, 3)
    desk.misses(T0 + 2 * HOUR, 5, label='stapler')               # a new thing: never a miss
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'missing' and last.misses == 3
    assert desk.prefs.values['sensitivity'] == NORMAL + 1


def test_a_new_things_bar_is_never_used(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.misses(T0 + HOUR, 5, label='stapler', conf=0.57)       # between its Easy and Normal bars
    desk.misses(T0 + 2 * HOUR, 5, label='lamp')                  # no character at all
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.misses == 0 and last.why == 'steady'


def test_when_you_move_the_slider_it_waits(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.tick()
    desk.prefs.values['sensitivity'] = 4
    desk.clock.advance(HOUR)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'waiting' and last.from_step == last.to_step == 4
    saved = desk.store.data[MEMORY_KEY]
    assert saved['step'] == 4 and saved['since'] == desk.clock.now()
    assert saved['hold_until'] == desk.clock.now() + settings.wait_after_you_change_hours * HOUR
    changed_at = desk.clock.now()
    desk.watch(changed_at - 2 * HOUR, 2)              # before your change: doesn't count
    desk.blinks(changed_at - 2 * HOUR, 40)
    desk.clock.advance(settings.wait_after_you_change_hours * HOUR - 1)
    desk.tick()
    assert desk.reviewer.last().why == 'waiting'      # type: ignore[union-attr]
    desk.clock.advance(1)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'not_enough_watching' and last.blinks == 0
    assert desk.prefs.values['sensitivity'] == 4


def test_when_off_it_moves_nothing_but_still_notices_you(settings, agents, object_states):
    desk = Desk(settings, agents, object_states, prefs={'sensitivity': NORMAL})
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.blinks(T0 + HOUR, 20)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'off' and desk.prefs.values['sensitivity'] == NORMAL
    desk.prefs.values['sensitivity'] = 1
    desk.tick()
    assert desk.store.data[MEMORY_KEY]['step'] == 1
    assert desk.store.data[MEMORY_KEY]['hold_until'] == desk.clock.now() + settings.wait_after_you_change_hours * HOUR


def test_no_slider_value_is_the_default_step(settings, agents, object_states):
    desk = Desk(settings, agents, object_states, prefs={'auto_sensitivity': True, 'sensitivity': 'picky'})
    desk.tick()
    assert desk.store.data[MEMORY_KEY]['step'] == agents.sensitivity_default


def test_the_clock_going_back_starts_the_stretch_again(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.tick()                                                    # since = T0
    desk.prefs.values['sensitivity'] = 1                           # you: since = T0, hold for a day
    desk.tick()
    desk.clock.set_wall_clock(T0 - 10 * 24 * HOUR)                 # ten days back
    desk.tick()
    saved = desk.store.data[MEMORY_KEY]
    now = desk.clock.now()
    assert saved['since'] == now
    assert saved['hold_until'] <= now + settings.wait_after_you_change_hours * HOUR


def test_a_broken_memory_is_logged_once_and_starts_over(settings, agents, object_states, caplog):
    whole = {'step': 2, 'since': 1.0, 'hold_until': None, 'last': None, 'moves': []}
    for broken in ('nonsense', {'step': 'two'}, {**whole, 'step': 'two'}, {**whole, 'step': 99},
                   {**whole, 'moves': 'none'}, {**whole, 'last': {'why': 'what'}}):
        store = InMemoryKeyValueStore({MEMORY_KEY: broken})
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=LOG):
            desk = Desk(settings, agents, object_states, store=store)
            assert desk.reviewer.last() is None and desk.reviewer.moves() == []
            desk.tick()
            desk.tick()
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
        assert store.data[MEMORY_KEY]['step'] == NORMAL


def test_it_keeps_only_changes_kept_moves_newest_first(settings, agents, object_states):
    desk = Desk(settings, agents, object_states, prefs={'sensitivity': 4, 'auto_sensitivity': True})
    desk.look_first()
    for _ in range(4):                                # moving the same way again needs no wait
        start = desk.clock.now()
        desk.clock.advance(3 * HOUR)
        desk.watch(start, 3)
        desk.blinks(start, 20)
        desk.tick()
    moves = desk.reviewer.moves()
    assert [m.to_step for m in moves] == [0, 1, 2] and len(moves) == settings.changes_kept
    assert len(desk.store.data[MEMORY_KEY]['moves']) == settings.changes_kept


def test_a_failed_save_of_the_slider_is_not_a_move(settings, agents, object_states, caplog):
    desk = Desk(settings, agents, object_states)

    def broken(changes):
        raise OSError('disk full')

    desk.look_first()
    desk.prefs.update = broken  # type: ignore[method-assign]
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.blinks(T0 + HOUR, 20)
    with caplog.at_level(logging.ERROR, logger=LOG):
        desk.tick()
    assert caplog.records and desk.reviewer.moves() == []
    saved = desk.store.data[MEMORY_KEY]
    assert saved['step'] == NORMAL and saved['moves'] == []


def test_a_restart_keeps_what_it_remembered(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.blinks(T0 + HOUR, 20)
    desk.tick()
    again = desk.make()
    assert again.last() == desk.reviewer.last() and again.moves() == desk.reviewer.moves()
    assert isinstance(again.last(), SensitivityVerdict)
    desk.clock.advance(HOUR)
    again.tick(desk.clock.now())                               # it knows its own move: no hold
    assert again.last().why == 'not_enough_watching'           # type: ignore[union-attr]


def test_it_waits_before_undoing_its_own_move(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(3 * HOUR)
    desk.watch(T0, 3)
    desk.blinks(T0, 20)
    desk.tick()                                                    # jumpy: Normal → Careful
    assert desk.prefs.values['sensitivity'] == NORMAL - 1
    moved_at = desk.clock.now()
    desk.clock.advance(3 * HOUR)
    desk.watch(moved_at, 3)
    desk.misses(moved_at, 3, conf=0.5)                             # sure enough for Normal, not for Careful
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'too_soon_to_undo' and not last.moved and last.misses == 3
    assert desk.prefs.values['sensitivity'] == NORMAL - 1 and len(desk.reviewer.moves()) == 1
    desk.clock.set_wall_clock(moved_at + settings.wait_before_undo_hours * HOUR - 1)
    desk.tick()
    assert desk.reviewer.last().why == 'too_soon_to_undo'          # type: ignore[union-attr]
    later = desk.clock.now()
    desk.clock.advance(3 * HOUR)
    desk.watch(later, 3)
    desk.misses(later, 3, conf=0.5)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'missing' and desk.prefs.values['sensitivity'] == NORMAL


def test_your_move_ends_the_wait_before_undoing(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(3 * HOUR)
    desk.watch(T0, 3)
    desk.blinks(T0, 20)
    desk.tick()
    saved = desk.store.data[MEMORY_KEY]
    assert saved['moved_way'] == -1
    assert saved['undo_after'] == desk.clock.now() + settings.wait_before_undo_hours * HOUR
    desk.prefs.values['sensitivity'] = 0
    desk.tick()
    saved = desk.store.data[MEMORY_KEY]
    assert saved['moved_way'] is None and saved['undo_after'] is None


def test_the_clock_going_back_never_makes_the_undo_wait_longer(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(3 * HOUR)
    desk.watch(T0, 3)
    desk.blinks(T0, 20)
    desk.tick()
    desk.clock.set_wall_clock(T0 - 10 * 24 * HOUR)
    desk.tick()
    assert desk.store.data[MEMORY_KEY]['undo_after'] <= desk.clock.now() + settings.wait_before_undo_hours * HOUR


def test_your_change_while_it_looks_is_kept(settings, agents, object_states, caplog):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.blinks(T0 + HOUR, 20)
    read = desk.log.presence

    def you_move_it_meanwhile(day, include_live=True):
        desk.prefs.values['sensitivity'] = 4
        return read(day, include_live)

    desk.log.presence = you_move_it_meanwhile  # type: ignore[method-assign]
    with caplog.at_level(logging.INFO, logger=LOG):
        desk.tick()
    assert desk.prefs.values['sensitivity'] == 4 and desk.reviewer.moves() == []
    assert any('moved' in r.getMessage() for r in caplog.records)
    desk.log.presence = read  # type: ignore[method-assign]
    desk.clock.advance(HOUR)
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.why == 'waiting' and last.from_step == 4


def test_things_that_joined_the_chat_are_read_from_the_event_log(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)
    desk.look_first()
    desk.clock.advance(4 * HOUR)
    desk.watch(T0 + HOUR, 3)
    desk.misses(T0 + HOUR, 3)
    for i in range(3):
        desk.log.write(AgentJoined(ts=T0 + HOUR + i * 600 + 10, agent='cup', name='Cup', label='cup', conf=0.42))
        desk.log.write(AgentLeft(ts=T0 + HOUR + i * 600 + 130, agent='cup', name='Cup', label='cup', stayed_sec=120))
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.misses == 0 and last.why == 'steady'


def test_off_the_desk_counts_only_when_only_on_desk_is_on(settings, agents, object_states):
    def on_desk(x, y, width, height):
        return False

    for only, misses in ((True, 0), (False, 3)):
        desk = Desk(settings, agents.model_copy(update={'only_on_desk': only}), object_states, on_desk=on_desk)
        desk.look_first()
        desk.clock.advance(4 * HOUR)
        desk.watch(T0 + HOUR, 3)
        desk.misses(T0 + HOUR, 3, size=(640, 480))
        desk.tick()
        last = desk.reviewer.last()
        assert last is not None and last.misses == misses


def test_a_blink_leaves_out_the_steps_gone_wait(settings, agents, object_states):
    desk = Desk(settings, agents, object_states)                     # gone_sec 3 × Normal's steady 1 = 3 s
    desk.look_first()
    desk.clock.advance(3 * HOUR)
    desk.watch(T0, 3)
    desk.history.append([removed(T0 + 60 * i, dwell=settings.blink_sec + 2) for i in range(1, 19)])
    desk.tick()
    last = desk.reviewer.last()
    assert last is not None and last.blinks == 18 and last.why == 'jumpy'
