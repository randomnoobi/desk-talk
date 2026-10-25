"""The plain data the rule engine reads, the rule_outcome event, and the two fakes' extra bookkeeping."""

from __future__ import annotations

import dataclasses

import pytest

from desktalk.domain import ObjectSnapshot, RuleHistory, RuleOutcome, StateChange, event_from_dict
from tests.fakes import FakeObjectStates, FakeRuleNudges


def test_rule_outcome_round_trips_through_a_dict():
    event = RuleOutcome(ts=100.0, rule='sip', habit='hydration', result='worked', after_sec=42.0)
    data = event.to_dict()
    assert data['type'] == 'rule_outcome'
    assert (data['rule'], data['habit'], data['result'], data['after_sec']) == ('sip', 'hydration', 'worked', 42.0)
    assert event_from_dict(data) == event


def test_the_rule_data_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ObjectSnapshot('cup', 'PRESENT', 1.0).state = 'ABSENT'
    history = RuleHistory(changes=[StateChange(1.0, 'cup', 'moved')], fired={'sip': [5.0]})
    with pytest.raises(dataclasses.FrozenInstanceError):
        history.fired = {}


def test_fake_object_states_counts_snapshots_and_lets_watched_since_change():
    states = FakeObjectStates()
    assert states.snapshot_calls == 0 and states.snapshot().watched_since is None
    states.watched_since = 9.0
    states.set('cup', 'PRESENT', 3.0)
    snap = states.snapshot()
    assert states.snapshot_calls == 2
    assert snap.watched_since == 9.0 and snap.objects['cup'] == ObjectSnapshot('cup', 'PRESENT', 3.0)


def test_fake_object_states_takes_starting_objects():
    states = FakeObjectStates([ObjectSnapshot('phone', 'ABSENT', None)], watched_since=1.0)
    assert states.snapshot().objects['phone'].state == 'ABSENT'


def test_fake_rule_nudges_hands_out_a_copy_and_records_what_fired():
    from desktalk.domain import Nudge
    nudge = Nudge(habit='hydration', key='sip', evidence='no sip', text='Sip?', repeat_after_sec=60)
    fake = FakeRuleNudges([nudge])
    assert fake.calls == 0
    got = fake.nudges(10.0)
    got.clear()
    assert fake.nudges(11.0) == [nudge] and fake.calls == 2
    fake.note_fired(nudge, 12.0)
    assert fake.fired == [(nudge, 12.0)]
