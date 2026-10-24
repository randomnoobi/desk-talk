"""The rule engine (desktalk/core/habits/rule_engine.py): rules + what's on the desk -> nudges."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import TypeAdapter

from desktalk.config import Condition, QuietWindow, RuleConfig, Success
from desktalk.core.habits.rule_engine import check_success, evaluate, holds, in_quiet_hours, lookback_sec
from desktalk.domain.rules import DeskSnapshot, HabitDone, ObjectSnapshot, RuleHistory, StateChange

T0 = 1_780_000_000.0        # 13:26 in Los Angeles (a summer afternoon, outside the quiet hours)
LA = 'America/Los_Angeles'
DRINKS = ['cup', 'mug', 'bottle', 'water bottle', 'wine glass']
UNTOUCHED = "the cup, mug, bottle, water bottle or wine glass hasn't been moved or removed in"

# The agreed hydration rule from the plan, with a fixed place so the test hour never lands in quiet hours.
HYDRATION = RuleConfig.model_validate({
    'id': 'hydration',
    'enabled': True,
    'habit': 'hydration',
    'timezone': LA,
    'gate': {'state': {'object': 'person', 'is': 'present'}},
    'when': {'all': [
        {'state': {'object': 'person', 'is': 'present', 'for': '60m'}},
        {'no_event': {'object': DRINKS, 'events': ['moved', 'removed'], 'for': '60m'}},
        {'no_habit': {'habit': 'hydration', 'for': '60m'}},
    ]},
    'cooldown': '30m',
    'max_nudges_per_hour': 4,
    'quiet_hours': {'start': '22:00', 'end': '07:00'},
    'message': "You've been at the desk for an hour without a drink. Time for some water?",
    'success': {'within': '5m', 'when': {'any': [
        {'event': {'object': DRINKS, 'events': ['moved', 'removed']}},
        {'habit': {'habit': 'hydration'}},
    ]}},
})


def cond(data: dict):
    return TypeAdapter(Condition).validate_python(data)


def rule(**changes) -> RuleConfig:
    """The hydration rule with some parts swapped (a plain dict for `success` is read like the YAML)."""
    if isinstance(changes.get('success'), dict):
        changes['success'] = Success.model_validate(changes['success'])
    return HYDRATION.model_copy(update=changes)


def desk(watched_since: float | None = T0, **objects: tuple[str, float | None]) -> DeskSnapshot:
    return DeskSnapshot({label: ObjectSnapshot(label, state, since) for label, (state, since) in objects.items()},
                        watched_since)


def history(changes=(), fired=None, habits=()) -> RuleHistory:
    return RuleHistory(tuple(changes), fired or {}, tuple(habits))


def change(ts: float, label: str, kind: str) -> StateChange:
    return StateChange(ts, label, kind)


def ts(text: str, zone: str, fold: int = 0) -> float:
    return datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo(zone), fold=fold).timestamp()


def state(who: str, is_: str, for_: str | None = None) -> dict:
    return {'state': {'object': who, 'is': is_, **({'for': for_} if for_ else {})}}


def no_event(who: str, for_: str, events=('moved', 'removed')) -> dict:
    return {'no_event': {'object': who, 'events': list(events), 'for': for_}}


# ── the hydration rule end to end ───────────────────────────────────────────────────────────────────────────────────
def test_hydration_nudges_after_an_hour_with_an_untouched_bottle():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0), watched_since=T0)
    [n] = evaluate([HYDRATION], snap, history(), T0 + 3600)
    assert n.key == 'rule.hydration' and n.habit == 'hydration' and n.repeat_after_sec == 1800
    assert n.text == HYDRATION.message
    assert n.evidence == (f'the person has been present for 60 minutes; {UNTOUCHED} 60 minutes; '
                          'no sip has been logged in over 60 minutes')


def test_not_yet_at_59_minutes():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0), watched_since=T0)
    assert evaluate([HYDRATION], snap, history(), T0 + 3599) == []


def test_a_sip_resets_the_hour():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0 + 1800), watched_since=T0)
    sip = [change(T0 + 1800, 'bottle', 'removed')]
    assert evaluate([HYDRATION], snap, history(sip), T0 + 3600) == []
    [n] = evaluate([HYDRATION], snap, history(sip), T0 + 5400)
    assert n.evidence == (f'the person has been present for 90 minutes; {UNTOUCHED} 60 minutes; '
                          'no sip has been logged in over 60 minutes')


def test_a_cup_moving_resets_the_hour():
    snap = desk(person=('PRESENT', T0), cup=('ABSENT', T0 + 1800), watched_since=T0)
    sip = [change(T0 + 1800, 'cup', 'removed')]
    assert evaluate([HYDRATION], snap, history(sip), T0 + 3600) == []


def test_a_logged_sip_resets_the_hour():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    logged = [HabitDone(T0 + 1800, 'hydration', 'sip')]
    assert evaluate([HYDRATION], snap, history(habits=logged), T0 + 3600) == []
    [n] = evaluate([HYDRATION], snap, history(habits=logged), T0 + 5400)
    assert n.key == 'rule.hydration'


def test_any_log_of_the_habit_resets_the_hour():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    logged = [HabitDone(T0 + 1800, 'hydration', 'glass')]
    assert evaluate([HYDRATION], snap, history(habits=logged), T0 + 3600) == []
    assert len(evaluate([HYDRATION], snap, history(habits=logged), T0 + 5400)) == 1


def test_another_habit_does_not_count():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    logged = [HabitDone(T0 + 1800, 'breaks', 'break')]
    assert len(evaluate([HYDRATION], snap, history(habits=logged), T0 + 3600)) == 1


def test_evidence_names_every_drink_and_the_sip():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    logged = [HabitDone(T0 + 1800, 'hydration', 'sip')]
    [n] = evaluate([HYDRATION], snap, history(habits=logged), T0 + 5400)
    assert n.evidence == ('the person has been present for 90 minutes; '
                          "the cup, mug, bottle, water bottle or wine glass hasn't been moved or removed in over "
                          '60 minutes; no sip has been logged in 60 minutes')


def test_a_change_to_another_object_or_kind_does_not_reset_the_hour():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0), watched_since=T0)
    other = [change(T0 + 1800, 'phone', 'removed'), change(T0 + 1900, 'bottle', 'appeared')]
    assert len(evaluate([HYDRATION], snap, history(other), T0 + 3600)) == 1


def test_gate_closed_when_you_are_away():
    snap = desk(person=('ABSENT', T0), bottle=('PRESENT', T0), watched_since=T0)
    assert evaluate([HYDRATION], snap, history(), T0 + 7200) == []


def test_no_event_needs_enough_watching():
    now = T0 + 7200
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0), watched_since=now - 300)
    assert evaluate([HYDRATION], snap, history(), now) == []


def test_never_seen_bottle_counts_as_absent_since_watching():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    [n] = evaluate([HYDRATION], snap, history(), T0 + 3600)
    assert f'{UNTOUCHED} 60 minutes' in n.evidence


def test_state_for_needs_the_whole_time():
    snap = desk(person=('PRESENT', T0 + 3600 - 1800), bottle=('PRESENT', T0), watched_since=T0)
    assert evaluate([HYDRATION], snap, history(), T0 + 3600) == []


def test_no_pictures_yet_means_no_for_check_and_no_no_event_check_is_true():
    snap = desk(watched_since=None, person=('PRESENT', None))
    assert not holds(cond(state('person', 'present', '1m')), snap, [], T0)
    assert not holds(cond(no_event('bottle', '1m')), snap, [], T0)
    assert not holds(cond(state('bottle', 'absent', '1m')), snap, [], T0)
    assert holds(cond(state('person', 'present')), snap, [], T0)


def test_a_state_that_is_not_known_does_not_pass_a_for_check():
    snap = desk(person=('PRESENT', None))
    assert not holds(cond(state('person', 'present', '1m')), snap, [], T0 + 600)


def test_disabled_rule_is_skipped():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0))
    assert evaluate([rule(enabled=False)], snap, history(), T0 + 3600) == []


def test_rules_come_out_in_the_order_they_are_written():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0))
    second = rule(id='second', message='second one')
    third = rule(id='third', message='third one')
    assert [n.key for n in evaluate([second, HYDRATION, third], snap, history(), T0 + 3600)] == [
        'rule.second', 'rule.hydration', 'rule.third']


def test_a_rule_without_quiet_hours_is_fine():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0))
    assert len(evaluate([rule(quiet_hours=None)], snap, history(), T0 + 3600)) == 1


# ── conditions ──────────────────────────────────────────────────────────────────────────────────────────────────────
def test_any_all_not():
    snap = desk(person=('PRESENT', T0), phone=('ABSENT', T0))
    yes, no = state('person', 'present'), state('phone', 'present')
    assert holds(cond({'all': [yes, yes]}), snap, [], T0)
    assert not holds(cond({'all': [yes, no]}), snap, [], T0)
    assert holds(cond({'any': [no, yes]}), snap, [], T0)
    assert not holds(cond({'any': [no, no]}), snap, [], T0)
    assert holds(cond({'not': no}), snap, [], T0)
    assert not holds(cond({'not': yes}), snap, [], T0)


def test_state_matches_the_state_machine_words_in_any_case():
    snap = desk(person=('UNKNOWN', T0), bottle=('PRESENT', T0))
    assert holds(cond(state('person', 'unknown')), snap, [], T0)
    assert holds(cond(state('bottle', 'present')), snap, [], T0)
    assert not holds(cond(state('bottle', 'absent')), snap, [], T0)


def test_an_object_missing_from_the_snapshot_is_absent_since_watching():
    snap = desk(watched_since=T0)
    assert holds(cond(state('bottle', 'absent')), snap, [], T0)
    assert holds(cond(state('bottle', 'absent', '30m')), snap, [], T0 + 1800)
    assert not holds(cond(state('bottle', 'absent', '30m')), snap, [], T0 + 1799)
    assert not holds(cond(state('bottle', 'present')), snap, [], T0)


def test_no_event_looks_only_at_the_last_for_seconds():
    snap = desk(watched_since=T0)
    old = [change(T0 + 100, 'bottle', 'moved')]
    assert not holds(cond(no_event('bottle', '10m')), snap, old, T0 + 600)
    assert holds(cond(no_event('bottle', '10m')), snap, old, T0 + 700)
    assert holds(cond(no_event('bottle', '10m', events=('removed',))), snap, old, T0 + 600)


def test_event_counts_only_after_events_after():
    snap = desk()
    seen = [change(T0 + 50, 'bottle', 'moved')]
    sip = cond({'event': {'object': 'bottle', 'events': ['moved']}})
    assert holds(sip, snap, seen, T0 + 100, events_after=T0)
    assert not holds(sip, snap, seen, T0 + 100, events_after=T0 + 60)
    assert not holds(sip, snap, [change(T0 + 50, 'bottle', 'removed')], T0 + 100, events_after=T0)
    assert not holds(sip, snap, [change(T0 + 50, 'phone', 'moved')], T0 + 100, events_after=T0)


def test_event_works_inside_all_any_and_not():
    snap = desk(person=('PRESENT', T0))
    seen = [change(T0 + 50, 'bottle', 'moved')]
    sip = {'event': {'object': 'bottle', 'events': ['moved']}}
    here = state('person', 'present')
    assert holds(cond({'all': [here, sip]}), snap, seen, T0 + 100, events_after=T0)
    assert holds(cond({'any': [state('person', 'absent'), sip]}), snap, seen, T0 + 100, events_after=T0)
    assert not holds(cond({'not': sip}), snap, seen, T0 + 100, events_after=T0)
    assert holds(cond({'not': sip}), snap, [], T0 + 100, events_after=T0)


def test_no_habit_needs_enough_watching_and_no_log_of_that_habit():
    sips = cond({'no_habit': {'habit': 'hydration', 'for': '10m'}})
    snap = desk(watched_since=T0)
    sip = [HabitDone(T0 + 100, 'hydration', 'sip')]
    assert not holds(sips, snap, [], T0 + 599)                       # not watched for 10 minutes yet
    assert holds(sips, snap, [], T0 + 600)
    assert not holds(sips, snap, [], T0 + 650, habits=sip)
    assert holds(sips, snap, [], T0 + 700, habits=sip)
    glass = [HabitDone(T0 + 100, 'hydration', 'glass')]
    assert not holds(sips, snap, [], T0 + 650, habits=glass)         # any log of the habit counts
    assert holds(sips, snap, [], T0 + 650, habits=[HabitDone(T0 + 100, 'breaks', 'break')])   # another habit doesn't
    assert not holds(sips, desk(watched_since=None), [], T0 + 700)


def test_habit_counts_only_after_events_after():
    sip = cond({'habit': {'habit': 'hydration'}})
    logged = [HabitDone(T0 + 50, 'hydration', 'sip')]
    assert holds(sip, desk(), [], T0 + 100, events_after=T0, habits=logged)
    assert not holds(sip, desk(), [], T0 + 100, events_after=T0 + 60, habits=logged)
    assert not holds(sip, desk(), [], T0 + 100, habits=logged)
    assert holds(sip, desk(), [], T0 + 100, events_after=T0, habits=[HabitDone(T0 + 50, 'hydration', 'glass')])
    assert not holds(sip, desk(), [], T0 + 100, events_after=T0, habits=[HabitDone(T0 + 50, 'breaks', 'break')])


def test_event_matches_any_of_its_objects():
    drink = cond({'event': {'object': ['cup', 'mug'], 'events': ['moved']}})
    assert holds(drink, desk(), [change(T0 + 50, 'mug', 'moved')], T0 + 100, events_after=T0)
    assert not holds(drink, desk(), [change(T0 + 50, 'bottle', 'moved')], T0 + 100, events_after=T0)


def test_event_without_events_after_is_never_true():
    sip = cond({'event': {'object': 'bottle', 'events': ['moved']}})
    assert not holds(sip, desk(), [change(T0 + 50, 'bottle', 'moved')], T0 + 100)


# ── evidence ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_evidence_for_a_state_without_for_and_a_missing_object():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    r = rule(when=cond({'all': [state('person', 'present'), state('bottle', 'absent', '10m'),
                                {'not': state('phone', 'present')}]}))
    [n] = evaluate([r], snap, history(), T0 + 659)
    assert n.evidence == 'the person is present; the bottle has been absent for 10 minutes'


def test_evidence_for_any_names_only_what_held_and_not_is_left_out():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    r = rule(when=cond({'any': [state('person', 'absent'), state('person', 'present', '5m'),
                                {'not': state('phone', 'present')}]}))
    [n] = evaluate([r], snap, history(), T0 + 330)
    assert n.evidence == 'the person has been present for 5 minutes'


def test_evidence_uses_the_right_words_for_each_change():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    now = T0 + 1800

    def said(events):
        [n] = evaluate([rule(when=cond(no_event('phone', '30m', events)))], snap, history(), now)
        return n.evidence

    assert said(['appeared']) == "the phone hasn't appeared in 30 minutes"
    assert said(['moved']) == "the phone hasn't been moved in 30 minutes"
    assert said(['removed']) == "the phone hasn't been removed in 30 minutes"
    assert said(['appeared', 'removed']) == "the phone hasn't appeared or been removed in 30 minutes"
    assert said(['moved', 'appeared']) == "the phone hasn't been moved or appeared in 30 minutes"


def test_evidence_says_1_minute_not_1_minutes():
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    r = rule(when=cond({'all': [state('person', 'present', '1m'), no_event('bottle', '1m')]}))
    [n] = evaluate([r], snap, history(), T0 + 60)
    assert n.evidence == "the person has been present for 1 minute; the bottle hasn't been moved or removed in 1 minute"


def test_evidence_does_not_claim_an_exact_time_older_than_the_history():
    # The bottle last moved 90 minutes ago, but the history only goes back an hour (the longest `for`), and the
    # camera has watched for five hours: the most it can honestly say is "over 60 minutes".
    now = T0 + 5 * 3600
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', now - 5400), watched_since=T0)
    [n] = evaluate([HYDRATION], snap, history(), now)
    assert n.evidence == (f'the person has been present for 300 minutes; {UNTOUCHED} over 60 minutes; '
                          'no sip has been logged in over 60 minutes')


def test_evidence_says_over_once_watching_is_longer_than_the_history():
    now = T0 + 3600 + 30                    # watched for just over the lookback (60 minutes)
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    [n] = evaluate([HYDRATION], snap, history(), now)
    assert "hasn't been moved or removed in over 60 minutes" in n.evidence
    assert 'no sip has been logged in over 60 minutes' in n.evidence
    [n] = evaluate([HYDRATION], snap, history(), T0 + 3600)        # exactly the lookback: the history covers it all
    assert "hasn't been moved or removed in 60 minutes" in n.evidence


def test_no_habit_evidence_never_counts_from_when_the_camera_started_watching():
    # No sip is in the history, so all that is known is "not within the last hour": never "70 minutes since the
    # camera started", because a sip has nothing to do with the camera.
    snap = desk(person=('PRESENT', T0), watched_since=T0)
    [n] = evaluate([HYDRATION], snap, history(), T0 + 4200)
    assert 'no sip has been logged in over 60 minutes' in n.evidence


def test_no_habit_evidence_uses_the_plain_words_of_the_habit():
    for habit, words in [('breaks', 'break'), ('food', 'food'), ('phone_free', 'phone-free stretch'),
                         ('light', 'sunlight'), ('hydration', 'sip')]:
        r = rule(habit=habit, when=cond({'no_habit': {'habit': habit, 'for': '3h'}}))
        [n] = evaluate([r], desk(person=('PRESENT', T0), watched_since=T0), history(), T0 + 4 * 3600)
        assert n.evidence == f'no {words} has been logged in over 180 minutes'


# ── cooldown and the hourly cap ─────────────────────────────────────────────────────────────────────────────────────
def test_cooldown_and_hourly_cap():
    now = T0 + 7200
    ready = desk(person=('PRESENT', T0), bottle=('PRESENT', T0), watched_since=T0)
    r = HYDRATION.model_copy(update={'cooldown': 600, 'max_nudges_per_hour': 2})
    assert evaluate([r], ready, history(fired={'hydration': [now - 599]}), now) == []
    assert len(evaluate([r], ready, history(fired={'hydration': [now - 600]}), now)) == 1
    assert evaluate([r], ready, history(fired={'hydration': [now - 3000, now - 1200]}), now) == []
    assert len(evaluate([r], ready, history(fired={'hydration': [now - 3601, now - 1200]}), now)) == 1


def test_another_rules_nudges_do_not_count():
    now = T0 + 7200
    ready = desk(person=('PRESENT', T0), bottle=('PRESENT', T0), watched_since=T0)
    assert len(evaluate([HYDRATION], ready, history(fired={'breaks': [now - 10]}), now)) == 1


# ── quiet hours ─────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize('local, quiet', [('21:59', False), ('22:00', True), ('02:00', True),
                                          ('06:59', True), ('07:00', False), ('13:00', False)])
def test_quiet_hours_wrap_past_midnight(local, quiet):
    window = QuietWindow(start='22:00', end='07:00')
    assert in_quiet_hours(window, LA, ts(f'2026-07-01 {local}', LA)) is quiet


def test_quiet_hours_that_do_not_wrap():
    window = QuietWindow(start='13:00', end='14:30')
    assert not in_quiet_hours(window, LA, ts('2026-07-01 12:59', LA))
    assert in_quiet_hours(window, LA, ts('2026-07-01 13:00', LA))
    assert in_quiet_hours(window, LA, ts('2026-07-01 14:29', LA))
    assert not in_quiet_hours(window, LA, ts('2026-07-01 14:30', LA))


def test_quiet_hours_stop_a_rule():
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0))
    now = T0 + 3600                                                 # 14:26 in Los Angeles
    assert evaluate([rule(quiet_hours=QuietWindow(start='14:00', end='15:00'))], snap, history(), now) == []
    assert len(evaluate([rule(quiet_hours=QuietWindow(start='15:00', end='16:00'))], snap, history(), now)) == 1


def test_quiet_hours_follow_daylight_saving():
    w = QuietWindow(start='22:00', end='07:00')
    assert in_quiet_hours(w, LA, ts('2026-07-01 22:30', LA))        # summer (PDT)
    assert in_quiet_hours(w, LA, ts('2026-01-15 22:30', LA))        # winter (PST)
    assert in_quiet_hours(w, LA, ts('2026-11-01 01:30', LA, fold=0))    # fall back: the first 01:30
    assert in_quiet_hours(w, LA, ts('2026-11-01 01:30', LA, fold=1))    # and the second one
    early = QuietWindow(start='02:30', end='05:00')
    assert not in_quiet_hours(early, LA, ts('2026-03-08 01:59', LA))
    assert in_quiet_hours(early, LA, ts('2026-03-08 03:00', LA))    # spring forward: begins at 03:00


def test_fixed_offset_ignores_daylight_saving():
    w = QuietWindow(start='22:00', end='07:00')
    assert in_quiet_hours(w, '+08:00', ts('2026-07-01 14:30', 'UTC'))       # 22:30 there
    assert in_quiet_hours(w, '+08:00', ts('2026-01-15 14:30', 'UTC'))       # still 22:30 there
    assert not in_quiet_hours(w, '+08:00', ts('2026-07-01 13:30', 'UTC'))   # 21:30 there


def test_a_one_digit_hour_is_read():
    assert in_quiet_hours(QuietWindow(start='7:30', end='8:00'), 'UTC', ts('2026-07-01 07:45', 'UTC'))


# ── success ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_success_worked_missed_and_waiting():
    fired = T0
    snap = desk(person=('PRESENT', T0), bottle=('PRESENT', T0))
    assert check_success(HYDRATION, fired, snap, [], fired + 60) is None
    assert check_success(HYDRATION, fired, snap, [change(fired + 120, 'bottle', 'removed')], fired + 130) == 'worked'
    assert check_success(HYDRATION, fired, snap, [change(fired - 5, 'bottle', 'removed')], fired + 300) == 'missed'


def test_a_sip_between_two_rounds_still_counts_at_the_next_round():
    snap = desk(person=('PRESENT', T0))
    assert check_success(HYDRATION, T0, snap, [change(T0 + 61, 'bottle', 'moved')], T0 + 119) == 'worked'


def test_a_sip_on_the_last_second_still_counts_but_a_late_one_does_not():
    snap = desk(person=('PRESENT', T0))
    assert check_success(HYDRATION, T0, snap, [change(T0 + 300, 'bottle', 'moved')], T0 + 300) == 'worked'
    assert check_success(HYDRATION, T0, snap, [change(T0 + 301, 'bottle', 'moved')], T0 + 360) == 'missed'


def test_break_style_success_on_state():
    r = rule(success={'within': '2m', 'when': state('person', 'absent')})
    assert check_success(r, T0, desk(person=('ABSENT', T0 + 90)), [], T0 + 100) == 'worked'
    assert check_success(r, T0, desk(person=('PRESENT', T0)), [], T0 + 100) is None
    assert check_success(r, T0, desk(person=('PRESENT', T0)), [], T0 + 120) == 'missed'


def test_a_late_check_does_not_count_a_state_seen_after_the_deadline():
    # The bottle is taken away at T+330, after the 5-minute window; the round runs at T+350.
    r = rule(success={'within': '5m', 'when': state('bottle', 'absent')})
    late = desk(person=('PRESENT', T0), bottle=('ABSENT', T0 + 330))
    assert check_success(r, T0, late, [], T0 + 350) == 'missed'
    on_time = desk(person=('PRESENT', T0), bottle=('ABSENT', T0 + 200))
    assert check_success(r, T0, on_time, [], T0 + 300) == 'worked'     # the round on the deadline still judges it


def test_a_check_inside_the_grace_after_the_deadline_still_judges_the_state():
    r = rule(success={'within': '5m', 'when': state('bottle', 'absent')})
    gone = desk(person=('PRESENT', T0), bottle=('ABSENT', T0 + 200))
    assert check_success(r, T0, gone, [], T0 + 302, grace_sec=5) == 'worked'
    assert check_success(r, T0, gone, [], T0 + 306, grace_sec=5) == 'missed'


def test_past_the_deadline_only_events_by_the_deadline_count():
    away = desk(person=('ABSENT', T0 + 100), bottle=('ABSENT', T0 + 100))
    sip = [change(T0 + 200, 'bottle', 'moved')]
    late = T0 + 360

    def result(when, changes=()):
        return check_success(rule(success={'within': '5m', 'when': when}), T0, away, list(changes), late)

    moved = {'event': {'object': 'bottle', 'events': ['moved']}}
    assert result(moved, sip) == 'worked'
    assert result({'any': [moved, state('person', 'absent')]}, sip) == 'worked'
    assert result({'any': [moved, state('person', 'absent')]}) == 'missed'
    assert result({'all': [moved, state('person', 'absent')]}, sip) == 'missed'
    assert result(state('person', 'absent')) == 'missed'
    assert result({'not': state('person', 'present')}) == 'missed'
    assert result(no_event('phone', '1m', ['appeared'])) == 'missed'
    assert result(moved, [change(T0 + 301, 'bottle', 'moved')]) == 'missed'


def test_success_by_a_logged_sip():
    snap = desk(person=('PRESENT', T0))
    sip = [HabitDone(T0 + 120, 'hydration', 'sip')]
    assert check_success(HYDRATION, T0, snap, [], T0 + 130, habits=sip) == 'worked'
    assert check_success(HYDRATION, T0, snap, [], T0 + 360, habits=sip) == 'worked'           # a late round
    late_sip = [HabitDone(T0 + 301, 'hydration', 'sip')]
    assert check_success(HYDRATION, T0, snap, [], T0 + 360, habits=late_sip) == 'missed'
    early_sip = [HabitDone(T0 - 5, 'hydration', 'sip')]
    assert check_success(HYDRATION, T0, snap, [], T0 + 300, habits=early_sip) == 'missed'


def test_success_by_a_mug():
    snap = desk(person=('PRESENT', T0))
    assert check_success(HYDRATION, T0, snap, [change(T0 + 90, 'mug', 'moved')], T0 + 120) == 'worked'


def test_a_rule_without_success_has_nothing_to_wait_for():
    assert check_success(rule(success=None), T0, desk(), [], T0 + 1) is None


# ── how far back the runner must look ───────────────────────────────────────────────────────────────────────────────
def test_lookback_is_the_longest_for_or_within():
    assert lookback_sec([HYDRATION]) == 3600
    long_success = rule(success={'within': '3h', 'when': state('person', 'absent')})
    assert lookback_sec([HYDRATION, long_success]) == 3 * 3600
    inside_not = rule(gate=cond({'not': {'all': [state('person', 'present', '90m')]}}))
    assert lookback_sec([inside_not]) == 5400
    long_habit = rule(when=cond({'all': [state('person', 'present', '60m'),
                                         {'no_habit': {'habit': 'hydration', 'for': '2h'}}]}))
    assert lookback_sec([HYDRATION, long_habit]) == 2 * 3600
    assert lookback_sec([]) == 0
