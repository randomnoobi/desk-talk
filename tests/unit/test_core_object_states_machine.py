"""The state machine: one object, one picture at a time, with made-up times."""

from dataclasses import replace

import pytest

from desktalk.core.object_states.machine import ObjectState, Rules, StepInput, step
from desktalk.domain.object_states import Phase

RULES = Rules(appear_sec=3, gone_sec=3, move_fraction=0.2, track_moves=True)
MIDDLE = (0.5, 0.5)
BOX = (10.0, 20.0, 30.0, 40.0)


def _seen(ts, center=MIDDLE, conf=0.6, bbox=BOX):
    return StepInput(ts=ts, seen=True, center=center, conf=conf, bbox=bbox)


def _gone(ts):
    return StepInput(ts=ts, seen=False, center=None, conf=None, bbox=None)


def _feed(state, inputs, rules=RULES):
    """Run the inputs in order and return the final state plus every transition."""
    transitions = []
    for inp in inputs:
        state, fired = step(state, inp, rules)
        transitions.extend(fired)
    return state, transitions


def _present(since=0.0, anchor=MIDDLE):
    return ObjectState('bottle', Phase.PRESENT, since=since, anchor=anchor, anchor_since=since)


def test_unknown_becomes_present_after_more_than_the_wait():
    state, fired = _feed(ObjectState('bottle'), [_seen(t) for t in (0, 1, 2, 3)])
    assert state.phase == Phase.UNKNOWN
    assert fired == []
    state, fired = step(state, _seen(3.1), RULES)
    assert state.phase == Phase.PRESENT
    assert [t.type for t in fired] == ['appeared']
    assert fired[0].prev_state == Phase.UNKNOWN
    assert fired[0].new_state == Phase.PRESENT
    assert fired[0].label == 'bottle'
    assert fired[0].ts == 3.1
    assert fired[0].bbox == BOX
    assert fired[0].conf_avg == 0.6
    assert state.since == 3.1
    assert state.pending_since is None
    assert state.anchor == MIDDLE
    assert state.conf_n == 0
    assert state.conf_sum == 0.0


def test_unknown_becomes_absent_quietly():
    state, fired = _feed(ObjectState('bottle'), [_gone(0), _gone(2), _gone(3.5)])
    assert state.phase == Phase.ABSENT
    assert fired == []


def test_present_to_absent_fires_removed_with_dwell():
    state = _present(since=3.5)
    state, fired = _feed(state, [_gone(10), _gone(12), _gone(13.5)])
    assert state.phase == Phase.ABSENT
    assert [t.type for t in fired] == ['removed']
    assert fired[0].prev_state == Phase.PRESENT
    assert fired[0].new_state == Phase.ABSENT
    assert fired[0].dwell_sec == 10.0
    assert state.since == 13.5


def test_removed_reports_the_last_box_seen():
    state = replace(_present(since=0), last_bbox=BOX)
    state, fired = _feed(state, [_gone(10), _gone(13.5)])
    assert fired[0].bbox == BOX


def test_one_seen_picture_resets_the_wait():
    state, fired = _feed(_present(), [_gone(5), _seen(7), _gone(8), _gone(11)])
    assert state.phase == Phase.PRESENT
    assert fired == []


def test_moved_over_20_percent_only():
    state = _present()
    state, fired = step(state, _seen(10, center=(0.65, 0.5)), RULES)
    assert fired == []
    assert state.anchor == MIDDLE
    state, fired = step(state, _seen(11, center=(0.75, 0.5)), RULES)
    assert [t.type for t in fired] == ['moved']
    assert fired[0].prev_state == Phase.PRESENT
    assert fired[0].new_state == Phase.PRESENT
    assert fired[0].dwell_sec == 11.0
    assert state.anchor == (0.75, 0.5)
    assert state.anchor_since == 11


def test_moved_uses_straight_line_distance():
    # 0.15 across and 0.15 down is about 0.21 away.
    state, fired = step(_present(), _seen(10, center=(0.65, 0.65)), RULES)
    assert [t.type for t in fired] == ['moved']


def test_absent_comes_back_with_appeared():
    state, fired = _feed(ObjectState('bottle', Phase.ABSENT, since=0), [_seen(t) for t in (10, 11, 12, 13.5)])
    assert state.phase == Phase.PRESENT
    assert [t.type for t in fired] == ['appeared']
    assert fired[0].prev_state == Phase.ABSENT
    assert fired[0].dwell_sec == 13.5


def test_person_rules_wait_longer_to_leave_and_never_move():
    rules = Rules(appear_sec=3, gone_sec=90, move_fraction=0.2, track_moves=False)
    state = _present(anchor=(0.1, 0.5))
    state, fired = step(state, _seen(1, center=(0.9, 0.5)), rules)
    assert fired == []
    state, fired = _feed(state, [_gone(10), _gone(70)], rules)
    assert state.phase == Phase.PRESENT
    assert fired == []
    state, fired = step(state, _gone(101), rules)
    assert [t.type for t in fired] == ['removed']


def test_restored_state_sets_its_anchor_quietly():
    state = ObjectState('bottle', Phase.PRESENT, since=0)
    state, fired = step(state, _seen(5, center=(0.9, 0.9)), RULES)
    assert fired == []
    assert state.anchor == (0.9, 0.9)
    assert state.anchor_since == 5


def test_confidence_average_covers_the_seen_pictures_since_the_last_change():
    state, fired = _feed(
        ObjectState('bottle'),
        [_seen(0, conf=0.5), _seen(2, conf=0.7), _seen(3.5, conf=0.9)],
    )
    assert [t.type for t in fired] == ['appeared']
    assert fired[0].conf_avg == pytest.approx(0.7)
    assert fired[0].dwell_sec is None


def test_unknown_wait_starts_again_when_the_answer_flips():
    state, fired = _feed(ObjectState('bottle'), [_seen(0), _seen(1), _seen(2), _gone(2.5), _gone(3.5), _gone(5)])
    assert state.phase == Phase.UNKNOWN
    assert fired == []
    state, fired = step(state, _gone(5.6), RULES)
    assert state.phase == Phase.ABSENT
    assert fired == []
