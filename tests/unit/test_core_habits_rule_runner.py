"""The rule runner (desktalk/core/habits/rule_runner.py): checks the rules once a round and keeps the nudges."""

from __future__ import annotations

from desktalk.config import RuleConfig, RuleEngineSettings
from desktalk.core.habits.rule_runner import RuleRunner
from desktalk.domain import Nudge, RuleOutcome
from desktalk.ports import PeriodicWorker, RuleNudges
from tests.fakes import FakeObjectStates, SyncEventBus

T0 = 1_780_000_000.0        # 13:26 in Los Angeles (a summer afternoon, outside the quiet hours)
LA = 'America/Los_Angeles'
WATCHED = T0 - 7200         # the camera has been watching for two hours

# The agreed hydration rule from the plan, with a fixed place so the test hour never lands in quiet hours.
HYDRATION = RuleConfig.model_validate({
    'id': 'hydration',
    'enabled': True,
    'habit': 'hydration',
    'timezone': LA,
    'gate': {'state': {'object': 'person', 'is': 'present'}},
    'when': {'all': [
        {'state': {'object': 'person', 'is': 'present', 'for': '60m'}},
        {'no_event': {'object': 'bottle', 'events': ['moved', 'removed'], 'for': '60m'}},
    ]},
    'cooldown': '30m',
    'max_nudges_per_hour': 4,
    'quiet_hours': {'start': '22:00', 'end': '07:00'},
    'message': "You've been at the desk for an hour and your bottle hasn't moved. Time for some water?",
    'success': {'within': '5m', 'when': {'event': {'object': 'bottle', 'events': ['moved', 'removed']}}},
})

# A break-style rule: it worked if the person got up (went absent) within two minutes.
BREAK = RuleConfig.model_validate({
    'id': 'break',
    'enabled': True,
    'habit': 'breaks',
    'timezone': LA,
    'gate': {'state': {'object': 'person', 'is': 'present'}},
    'when': {'state': {'object': 'person', 'is': 'present', 'for': '30m'}},
    'cooldown': '30m',
    'max_nudges_per_hour': 2,
    'message': 'Time to stand up for a minute.',
    'success': {'within': '2m', 'when': {'state': {'object': 'person', 'is': 'absent'}}},
})

SETTINGS = RuleEngineSettings(check_every_sec=60)


def desk() -> FakeObjectStates:
    """A person who has sat for two hours and a bottle nobody has touched."""
    states = FakeObjectStates(watched_since=WATCHED)
    states.set('person', 'PRESENT', WATCHED)
    return states


def make(states: FakeObjectStates, *rules: RuleConfig) -> tuple[RuleRunner, SyncEventBus]:
    bus = SyncEventBus()
    return RuleRunner(rules or (HYDRATION,), states, bus, SETTINGS), bus


def outcomes(bus: SyncEventBus) -> list[RuleOutcome]:
    return [e for e in bus.published if isinstance(e, RuleOutcome)]


def summary(outcome: RuleOutcome) -> tuple:
    """Every field but the event's own id."""
    return outcome.ts, outcome.rule, outcome.habit, outcome.result, outcome.after_sec


def test_runner_returns_the_engines_nudges():
    runner, _ = make(desk())
    runner.tick(T0)
    found = runner.nudges(T0)
    assert [n.key for n in found] == ['rule.hydration']
    assert found[0].habit == 'hydration'


def test_rules_run_only_on_tick():
    states = desk()
    runner, _ = make(states)
    assert runner.nudges(T0) == []
    assert states.snapshot_calls == 0
    runner.tick(T0)
    assert states.snapshot_calls == 1
    for i in range(10):
        runner.nudges(T0 + i)
    assert states.snapshot_calls == 1
    states.change(T0 + 10, 'bottle', 'removed')
    assert len(runner.nudges(T0 + 30)) == 1          # nothing re-checked between rounds
    runner.tick(T0 + 60)
    assert runner.nudges(T0 + 60) == []


def test_nudges_hands_out_a_copy():
    runner, _ = make(desk())
    runner.tick(T0)
    runner.nudges(T0).clear()
    assert len(runner.nudges(T0)) == 1


def test_note_fired_takes_it_off_the_due_list():
    runner, _ = make(desk())
    runner.tick(T0)
    runner.note_fired(runner.nudges(T0)[0], T0)
    assert runner.nudges(T0 + 1) == []
    runner.tick(T0 + 60)                              # still in the cooldown
    assert runner.nudges(T0 + 60) == []
    runner.tick(T0 + 1800)
    assert [n.key for n in runner.nudges(T0 + 1800)] == ['rule.hydration']


def test_note_fired_ignores_old_protocols_nudges():
    runner, bus = make(desk())
    runner.tick(T0)
    old = Nudge(habit='hydration', key='hydration.pace', evidence='', text='Drink', repeat_after_sec=1800)
    runner.note_fired(old, T0)
    assert [n.key for n in runner.nudges(T0)] == ['rule.hydration']
    runner.tick(T0 + 60)                              # no fire was recorded, so no cooldown and no check
    assert [n.key for n in runner.nudges(T0 + 60)] == ['rule.hydration']
    runner.tick(T0 + 1000)
    assert outcomes(bus) == []


def test_outcome_worked_is_published():
    states = desk()
    runner, bus = make(states)
    runner.tick(T0)
    runner.note_fired(runner.nudges(T0)[0], T0)
    states.change(T0 + 120, 'bottle', 'removed')
    runner.tick(T0 + 120)
    assert [summary(o) for o in outcomes(bus)] == [(T0 + 120, 'hydration', 'hydration', 'worked', 120)]
    runner.tick(T0 + 180)
    assert len(outcomes(bus)) == 1


def test_outcome_missed_after_within():
    runner, bus = make(desk())
    runner.tick(T0)
    runner.note_fired(runner.nudges(T0)[0], T0)
    runner.tick(T0 + 240)
    assert outcomes(bus) == []                        # still waiting
    runner.tick(T0 + 301)
    assert [summary(o) for o in outcomes(bus)] == [(T0 + 301, 'hydration', 'hydration', 'missed', 301)]
    runner.tick(T0 + 361)
    assert len(outcomes(bus)) == 1


def test_success_is_checked_while_you_are_away():
    states = desk()
    runner, bus = make(states, BREAK)
    runner.tick(T0)
    runner.note_fired(runner.nudges(T0)[0], T0)
    states.set('person', 'ABSENT', T0 + 30)           # the person gets up
    runner.tick(T0 + 60)
    assert [o.result for o in outcomes(bus)] == ['worked']
    assert outcomes(bus)[0].rule == 'break'


def test_runner_passes_the_periodic_worker_contract():
    runner, _ = make(desk())
    assert isinstance(runner, PeriodicWorker)
    assert isinstance(runner, RuleNudges)
    assert runner.name == 'rule engine'
    assert runner.tick(T0) == 60


def test_sip_between_rounds_counts_as_success():
    states = desk()
    runner, bus = make(states)
    runner.tick(T0)
    runner.note_fired(runner.nudges(T0)[0], T0)
    states.change(T0 + 20, 'bottle', 'removed')
    states.change(T0 + 40, 'bottle', 'appeared')
    runner.tick(T0 + 60)
    assert [o.result for o in outcomes(bus)] == ['worked']
