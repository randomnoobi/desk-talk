"""The Protocols habit rules (desktalk/core/habits/protocols.py): pure functions, made-up numbers.

Times are on a fixed day (15 June 2026, local time) so the tests don't depend on today's date.
Wake 07:00, bed 22:30, sunset 18:30 unless a test says otherwise.
"""

import shutil
import time
from dataclasses import replace
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.habits import protocols as P
from desktalk.domain import Nudge
from desktalk.util import at_time_today

ROOT = Path(__file__).resolve().parents[2]
MIN = 60
HOUR = 3600
DAY = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))


@pytest.fixture(scope='module')
def rules(tmp_path_factory):
    """config/protocols.yaml as it is in the repo."""
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={}).protocols


def at(hhmm, days_later=0):
    return at_time_today(hhmm, DAY) + days_later * 86400


def day_state(now, **kw):
    wake, bed = P.day_frame(now, '07:00', '22:30')
    base = {'at_desk': True, 'session_start': wake, 'wake_ts': wake, 'bed_ts': bed, 'sunset_ts': at('18:30')}
    return P.RuleState(**{**base, **kw})


def keys(nudges):
    return [n.key for n in nudges]


def changed(section, **values):
    """A copy of one protocols.yaml section with some settings changed."""
    return section.model_copy(update=values)


# ── the waking day ────────────────────────────────────────────────────
def test_day_frame_handles_bedtime_after_midnight():
    now = at('00:20')
    wake, bed = P.day_frame(now, '08:00', '00:30')
    assert wake < now < bed                    # 00:20 still belongs to yesterday's waking day
    assert bed - wake == pytest.approx(16.5 * HOUR)


def test_day_frame_before_waking_is_still_yesterday():
    wake, bed = P.day_frame(at('05:00'), '07:00', '22:30')
    assert wake == at('07:00', days_later=-1) and bed == at('22:30', days_later=-1)


# ── 1. hydration ──────────────────────────────────────────────────────
def test_hydration_morning_and_pace(rules):
    s = day_state(at('07:20'), water_ml=0)
    assert 'hydration.morning' in keys(P.hydration_nudges(s, rules.hydration, at('07:20')))
    # 13:00: 6 h after waking, pace = 1.44 L; only 0.4 L, last sip 50 minutes ago
    now = at('13:00')
    n = P.hydration_nudges(day_state(now, water_ml=400, last_sip_ts=now - 50 * MIN), rules.hydration, now)
    assert keys(n) == ['hydration.pace']
    assert '50 minutes' in n[0].evidence and '1.4 L' in n[0].evidence
    assert n[0].repeat_after_sec == 45 * MIN
    # on pace: nothing to say
    assert P.hydration_nudges(day_state(now, water_ml=1500, last_sip_ts=now - 50 * MIN), rules.hydration, now) == []
    # recent sip: nothing to say
    assert P.hydration_nudges(day_state(now, water_ml=400, last_sip_ts=now - 10 * MIN), rules.hydration, now) == []


def test_hydration_morning_nudge_is_once_a_day(rules):
    n = P.hydration_nudges(day_state(at('07:20'), water_ml=0), rules.hydration, at('07:20'))
    assert n[0].once_per_day and n[0].speakers == ('hydration',)
    assert n[0].repeat_after_sec == 45 * MIN          # hydration.remind_after_min


def test_hydration_stops_two_hours_before_bed(rules):
    now = at('21:00')                       # bed at 22:30
    s = day_state(now, water_ml=0, last_sip_ts=now - 3 * HOUR)
    assert P.hydration_nudges(s, rules.hydration, now) == []
    now = at('20:20')
    assert keys(P.hydration_nudges(day_state(now, water_ml=0, last_sip_ts=now - 3 * HOUR), rules.hydration, now)) \
        == ['hydration.pace']


def test_hydration_quiet_time_counts_from_when_the_camera_started(rules):
    now = at('13:00')
    s = day_state(now, water_ml=0, session_start=now - 20 * MIN)       # no sip yet, but we only just started
    assert P.hydration_nudges(s, rules.hydration, now) == []


def test_hydration_cup_on_desk_wording(rules):
    now = at('12:00')
    s = day_state(now, water_ml=0, last_sip_ts=now - 60 * MIN, drink_on_desk=True)
    assert 'Nobody has picked me up' in P.hydration_nudges(s, rules.hydration, now)[0].text
    s = replace(s, drink_on_desk=False)
    assert "I haven't seen you drink anything" in P.hydration_nudges(s, rules.hydration, now)[0].text


def test_hydration_waits_while_nobody_is_at_the_desk(rules):
    now = at('13:00')
    s = day_state(now, at_desk=False, water_ml=0, last_sip_ts=now - 3 * HOUR)
    assert P.hydration_nudges(s, rules.hydration, now) == []


def test_water_on_waking_window_comes_from_protocols_yaml(rules):
    now = at('07:45')
    s = day_state(now, water_ml=0)
    assert 'hydration.morning' in keys(P.hydration_nudges(s, rules.hydration, now))
    shorter = changed(rules.hydration, on_waking_window_min=30)
    assert 'hydration.morning' not in keys(P.hydration_nudges(s, shorter, now))


def test_water_on_waking_done_share_comes_from_protocols_yaml(rules):
    now = at('07:20')
    s = day_state(now, water_ml=400)                       # 400 of the 600 ml on waking
    assert 'hydration.morning' not in keys(P.hydration_nudges(s, rules.hydration, now))      # half is done
    stricter = changed(rules.hydration, on_waking_done_share=0.75)
    assert 'hydration.morning' in keys(P.hydration_nudges(s, stricter, now))


# ── 2. breaks ─────────────────────────────────────────────────────────
def test_breaks_after_30_minutes_sitting(rules):
    now = at('10:00')
    assert P.break_nudges(day_state(now, sit_since=now - 25 * MIN), rules.breaks, now) == []
    n = P.break_nudges(day_state(now, sit_since=now - 34 * MIN), rules.breaks, now)
    assert keys(n) == ['breaks.sit'] and '34 minutes' in n[0].text and 'phone' in n[0].text
    assert n[0].repeat_after_sec == 15 * MIN and n[0].speakers == ('breaks', 'screens')
    # not at the desk: no nudge
    assert P.break_nudges(day_state(now, at_desk=False, sit_since=now - 40 * MIN), rules.breaks, now) == []


def test_break_nudge_leaves_out_the_phone_note_when_switched_off(rules):
    now = at('10:00')
    no_note = changed(rules.breaks, no_phone_on_breaks=False)
    assert 'phone' not in P.break_nudges(day_state(now, sit_since=now - 34 * MIN), no_note, now)[0].text


def test_walk_after_a_meal(rules):
    now = at('12:30')
    n = P.break_nudges(day_state(now, meal_done_ts=now - 2 * MIN, meal_name='sandwich'), rules.breaks, now)
    assert n and n[0].key == f'breaks.meal.{int(now - 2 * MIN)}' and 'sandwich' in n[0].text
    assert n[0].speakers == ('breaks', 'food') and n[0].repeat_after_sec == 6 * HOUR
    away = day_state(now, at_desk=False, meal_done_ts=now - 2 * MIN, meal_name='sandwich')
    assert P.break_nudges(away, rules.breaks, now) == []
    off = changed(rules.breaks, walk_after_meal=False)
    assert P.break_nudges(day_state(now, meal_done_ts=now - 2 * MIN, meal_name='sandwich'), off, now) == []


def test_walk_window_after_a_meal_comes_from_protocols_yaml(rules):
    now = at('12:30')
    s = day_state(now, meal_done_ts=now - 12 * MIN, meal_name='sandwich')
    assert P.break_nudges(s, rules.breaks, now) == []                   # 12 min > 10 min window
    longer = changed(rules.breaks, walk_window_after_meal_min=15)
    assert keys(P.break_nudges(s, longer, now)) == [f'breaks.meal.{int(now - 12 * MIN)}']


def test_walk_repeat_comes_from_protocols_yaml(rules):
    now = at('12:30')
    s = day_state(now, meal_done_ts=now - 2 * MIN)
    n = P.break_nudges(s, changed(rules.breaks, walk_repeat_after_hours=2), now)
    assert n[0].repeat_after_sec == 2 * HOUR


# ── 3. light ──────────────────────────────────────────────────────────
def test_morning_sunlight(rules):
    now = at('07:25')
    n = P.light_nudges(day_state(now), rules.light, now)
    assert 'light.morning' in keys(n)
    assert '10 min' in n[0].text and 'window' in n[0].text
    assert n[0].once_per_day and n[0].repeat_after_sec == 60 * MIN
    # already logged → nothing
    assert 'light.morning' not in keys(P.light_nudges(day_state(now, sunlight_ts=now - 600), rules.light, now))
    # 11:00 is too late for the morning rule
    assert 'light.morning' not in keys(P.light_nudges(day_state(at('11:00')), rules.light, at('11:00')))


def test_morning_sunlight_grace_comes_from_protocols_yaml(rules):
    now = at('08:25')                        # 85 minutes after waking: 60 min window + 30 min grace
    assert 'light.morning' in keys(P.light_nudges(day_state(now), rules.light, now))
    assert 'light.morning' not in keys(P.light_nudges(day_state(at('08:35')), rules.light, at('08:35')))
    no_grace = changed(rules.light, morning_grace_min=0)
    assert 'light.morning' not in keys(P.light_nudges(day_state(now), no_grace, now))


def test_afternoon_light_before_sunset(rules):
    now = at('17:30')                     # sunset 18:30
    n = P.light_nudges(day_state(now), rules.light, now)
    assert keys(n) == ['light.afternoon'] and n[0].once_per_day
    assert 'light.afternoon' not in keys(P.light_nudges(day_state(at('15:00')), rules.light, at('15:00')))
    assert P.light_nudges(day_state(now, afternoon_light_ts=now - 600), rules.light, now) == []
    assert P.light_nudges(day_state(now), changed(rules.light, afternoon=False), now) == []


def test_sunset_guess_without_a_sunset_time_comes_from_protocols_yaml(rules):
    now = at('17:30')
    s = day_state(now, sunset_ts=None)       # guessed: 4 hours before the 22:30 bedtime = 18:30
    assert keys(P.light_nudges(s, rules.light, now)) == ['light.afternoon']
    later_guess = changed(rules.light, sunset_guess_before_bed_hours=2)      # 20:30: too early for it
    assert P.light_nudges(s, later_guess, now) == []


def test_evening_bright_room(rules):
    now = at('21:45')
    bright = day_state(now, lux=400, bright_since=now - 10 * MIN)
    n = P.light_nudges(bright, rules.light, now)
    assert keys(n) == ['light.evening'] and 'Bed is in 45 minutes' in n[0].text
    assert n[0].repeat_after_sec == 60 * MIN
    dim = day_state(now, lux=40, bright_since=None)
    assert P.light_nudges(dim, rules.light, now) == []
    # cameras without lux use picture brightness
    level = day_state(now, brightness=200, bright_since=now - 10 * MIN)
    assert keys(P.light_nudges(level, rules.light, now)) == ['light.evening']


def test_evening_after_sunset_but_long_before_bed(rules):
    now = at('19:00')
    n = P.light_nudges(day_state(now, lux=400, bright_since=now - 10 * MIN), rules.light, now)
    assert keys(n) == ['light.evening'] and n[0].text.startswith('The sun has set')


def test_evening_bright_wait_comes_from_protocols_yaml(rules):
    now = at('21:45')
    s = day_state(now, lux=400, bright_since=now - 4 * MIN)
    assert P.light_nudges(s, rules.light, now) == []                     # 4 of 5 minutes
    sooner = changed(rules.light, evening_bright_min=3)
    n = P.light_nudges(s, sooner, now)
    assert keys(n) == ['light.evening'] and 'over 3 minutes' in n[0].evidence


def test_bed_soon_wording_comes_from_protocols_yaml(rules):
    now = at('21:45')
    s = day_state(now, lux=400, bright_since=now - 10 * MIN)
    late_only = changed(rules.light, bed_soon_min=30)
    assert P.light_nudges(s, late_only, now)[0].text.startswith('The sun has set')


def test_daytime_dim_room(rules):
    now = at('11:00')
    n = P.light_nudges(day_state(now, sunlight_ts=now - HOUR, lux=30, dim_since=now - 15 * MIN), rules.light, now)
    assert keys(n) == ['light.daytime_dim'] and n[0].repeat_after_sec == 60 * MIN
    # not dim for long enough yet
    assert P.light_nudges(day_state(now, sunlight_ts=now - HOUR, lux=30, dim_since=now - 5 * MIN), rules.light,
                          now) == []
    # cameras without lux use picture brightness
    s = day_state(now, sunlight_ts=now - HOUR, brightness=30, dim_since=now - 15 * MIN)
    assert keys(P.light_nudges(s, rules.light, now)) == ['light.daytime_dim']


def test_daytime_dim_margins_come_from_protocols_yaml(rules):
    early = at('07:50')                      # 50 minutes after waking
    s = day_state(early, sunlight_ts=early - 600, lux=30, dim_since=early - 15 * MIN)
    assert P.light_nudges(s, rules.light, early) == []
    late = at('17:45')                       # 45 minutes before sunset
    t = day_state(late, afternoon_light_ts=late - 600, lux=30, dim_since=late - 15 * MIN)
    assert P.light_nudges(t, rules.light, late) == []
    smaller = changed(rules.light, day_dim_margin_min=30)
    assert keys(P.light_nudges(s, smaller, early)) == ['light.daytime_dim']
    assert keys(P.light_nudges(t, smaller, late)) == ['light.daytime_dim']


def test_light_rules_wait_while_nobody_is_at_the_desk(rules):
    now = at('07:25')
    assert P.light_nudges(day_state(now, at_desk=False), rules.light, now) == []


def test_screens_off_an_hour_before_bed(rules):
    now = at('21:50')
    n = P.screen_nudges(day_state(now, screen_on_desk=True), rules.light, now)
    assert keys(n) == ['screens.bedtime'] and n[0].once_per_day and n[0].speakers == ('screens',)
    assert n[0].repeat_after_sec == 60 * MIN
    assert 'Bed is at' in n[0].evidence
    assert P.screen_nudges(day_state(at('20:00'), screen_on_desk=True), rules.light, at('20:00')) == []
    assert P.screen_nudges(day_state(now, screen_on_desk=False), rules.light, now) == []


def test_screens_after_bedtime_until_the_limit_from_protocols_yaml(rules):
    now = at('01:00', days_later=1)          # 2.5 hours after the 22:30 bedtime
    s = day_state(now, screen_on_desk=True)
    n = P.screen_nudges(s, rules.light, now)
    assert keys(n) == ['screens.bedtime'] and n[0].evidence.startswith('It is past your bedtime')
    sooner = changed(rules.light, screens_until_after_bed_hours=2)
    assert P.screen_nudges(s, sooner, now) == []


# ── 4. phone-free time ────────────────────────────────────────────────
def test_phone_on_desk(rules):
    now = at('15:00')
    assert P.phone_nudges(day_state(now, phone_on_desk_since=now - 2 * MIN), rules.phone_free, now) == []
    n = P.phone_nudges(day_state(now, phone_on_desk_since=now - 7 * MIN), rules.phone_free, now)
    assert keys(n) == ['phone.focus'] and '7 minutes' in n[0].text
    assert n[0].repeat_after_sec == 25 * MIN and n[0].speakers == ('phone_free', 'tasks')
    # last hour before bed: the bedtime version
    late = at('22:00')
    n = P.phone_nudges(day_state(late, phone_on_desk_since=late - 3 * MIN), rules.phone_free, late)
    assert keys(n) == ['phone.bedtime'] and n[0].once_per_day and n[0].repeat_after_sec == 25 * MIN
    # phone is the camera: never
    s = day_state(now, phone_on_desk_since=now - 30 * MIN, phone_counts=False)
    assert P.phone_nudges(s, rules.phone_free, now) == []


def test_phone_before_bed_wait_comes_from_protocols_yaml(rules):
    late = at('22:00')
    s = day_state(late, phone_on_desk_since=late - 3 * MIN)
    longer = changed(rules.phone_free, before_bed_on_desk_min=5)
    assert P.phone_nudges(s, longer, late) == []          # 3 of 5 minutes, and too soon for the focus nudge


def test_phone_bedtime_nudge_stops_after_bed_as_set_in_protocols_yaml(rules):
    now = at('00:30', days_later=1)          # 2 hours after the 22:30 bedtime
    s = day_state(now, phone_on_desk_since=now - 10 * MIN)
    assert keys(P.phone_nudges(s, rules.phone_free, now)) == ['phone.bedtime']
    sooner = changed(rules.phone_free, before_bed_until_after_bed_hours=1)
    assert keys(P.phone_nudges(s, sooner, now)) == ['phone.focus']


# ── kept from the original reminders ──────────────────────────────────
def test_original_food_and_task_reminders(rules):
    now = at('14:00')
    n = P.food_nudges(day_state(now, food_since=now - 25 * MIN, food_name='donut'), rules.food, now)
    assert n[0].evidence == 'A donut has been on the desk for 25 minutes.'
    assert n[0].key == f'food.sitting.{int(now - 25 * MIN)}' and n[0].repeat_after_sec == 6 * HOUR
    assert P.food_nudges(day_state(now, food_since=now - 15 * MIN, food_name='donut'), rules.food, now) == []
    assert P.task_nudges(day_state(now, open_todos=()), rules.tasks, now) == []
    n = P.task_nudges(day_state(now, open_todos=('Essay',)), rules.tasks, now)
    assert n[0].text.startswith('You still have 1 open task,') and n[0].repeat_after_sec == 40 * MIN
    two = P.task_nudges(day_state(now, open_todos=('Essay', '', 'Email')), rules.tasks, now)
    assert two[0].text.startswith('You still have 2 open tasks, like "Essay"')


def test_food_repeat_comes_from_protocols_yaml(rules):
    now = at('14:00')
    s = day_state(now, food_since=now - 25 * MIN, food_name='donut')
    assert P.food_nudges(s, changed(rules.food, repeat_after_hours=2), now)[0].repeat_after_sec == 2 * HOUR


def test_how_much_of_a_task_name_is_shown_comes_from_protocols_yaml(rules):
    now = at('14:00')
    s = day_state(now, open_todos=('Write the history essay about the river towns',))
    n = P.task_nudges(s, changed(rules.tasks, name_shown_chars=9), now)
    assert 'like "Write the"' in n[0].text and '"Write the h' not in n[0].text


def test_task_check_in_waits_until_the_camera_has_watched_long_enough(rules):
    now = at('14:00')
    assert P.task_nudges(day_state(now, open_todos=('Essay',), session_start=now - 30 * MIN), rules.tasks, now) == []
    assert P.task_nudges(day_state(now, open_todos=('Essay',), at_desk=False), rules.tasks, now) == []


@pytest.mark.parametrize('section, rule, extra', [
    ('hydration', P.hydration_nudges, {'water_ml': 0, 'last_sip_ts': at('10:00')}),
    ('breaks', P.break_nudges, {'sit_since': at('12:00')}),
    ('light', P.light_nudges, {'lux': 30, 'dim_since': at('12:00')}),
    ('light', P.screen_nudges, {'screen_on_desk': True}),
    ('phone_free', P.phone_nudges, {'phone_on_desk_since': at('12:00')}),
    ('food', P.food_nudges, {'food_since': at('12:00'), 'food_name': 'donut'}),
    ('tasks', P.task_nudges, {'open_todos': ('Essay',)}),
])
def test_a_switched_off_habit_never_nudges(rules, section, rule, extra):
    now = at('13:00') if rule is not P.screen_nudges else at('22:00')
    s = day_state(now, **extra)
    assert rule(s, getattr(rules, section), now)                   # on: there is something to say
    assert rule(s, changed(getattr(rules, section), enabled=False), now) == []


# ── choosing which one to send ────────────────────────────────────────
def test_ready_nudges_respect_cooldowns_and_least_recent_habit():
    now = at('13:00')
    a = Nudge('hydration', 'hydration.pace', 'e', 't', repeat_after_sec=45 * MIN)
    b = Nudge('breaks', 'breaks.sit', 'e', 't', repeat_after_sec=15 * MIN)
    c = Nudge('light', 'light.morning', 'e', 't', repeat_after_sec=60 * MIN, once_per_day=True)
    assert P.ready_nudges([a, b], {}, now)[0] is a                         # nothing said yet: the first one
    last = {'breaks.sit': now - 5 * MIN, 'habit:breaks': now - 5 * MIN}
    assert P.ready_nudges([a, b], last, now)[0] is a                       # breaks said 5 min ago
    last = {'habit:hydration': now - 60 * MIN, 'habit:breaks': now - 90 * MIN}
    assert P.ready_nudges([a, b], last, now)[0] is b                       # least recently heard habit first
    assert P.ready_nudges([c], {'light.morning': now - 3 * HOUR}, now) == []   # once a day
    assert P.ready_nudges([c], {'light.morning': now - 24 * HOUR}, now)[0] is c     # yesterday's doesn't count
    assert P.ready_nudges([], {}, now) == []


def test_ready_nudges_are_in_the_order_to_send_them():
    now = at('13:00')
    a = Nudge('hydration', 'hydration.pace', 'e', 't', repeat_after_sec=45 * MIN)
    b = Nudge('breaks', 'breaks.sit', 'e', 't', repeat_after_sec=15 * MIN)
    c = Nudge('food', 'food.sitting.1', 'e', 't', repeat_after_sec=6 * HOUR)
    last = {'habit:hydration': now - 10 * MIN, 'habit:breaks': now - 90 * MIN,
            'food.sitting.1': now - HOUR, 'habit:food': now - HOUR}
    assert P.ready_nudges([a, b, c], last, now) == [b, a]


def test_a_once_a_day_nudge_also_waits_its_repeat_time_after_midnight():
    c = Nudge('screens', 'screens.bedtime', 'e', 't', repeat_after_sec=60 * MIN, once_per_day=True)
    fired = at('23:50')
    assert P.ready_nudges([c], {'screens.bedtime': fired}, at('00:20', days_later=1)) == []
    assert P.ready_nudges([c], {'screens.bedtime': fired}, at('00:55', days_later=1))[0] is c


def test_candidate_nudges_runs_every_rule(rules):
    now = at('13:00')
    s = day_state(now, water_ml=0, last_sip_ts=now - 2 * HOUR, sit_since=now - 40 * MIN,
                  phone_on_desk_since=now - 10 * MIN, open_todos=('x',), food_since=now - HOUR, food_name='donut',
                  lux=30, dim_since=now - HOUR)
    habits = {n.habit for n in P.candidate_nudges(s, rules, now)}
    assert habits == {'hydration', 'breaks', 'light', 'phone_free', 'food', 'tasks'}
    late = at('22:00')
    s = day_state(late, screen_on_desk=True)
    assert 'screens' in {n.habit for n in P.candidate_nudges(s, rules, late)}


def test_bright_and_dim_prefer_lux_over_picture_brightness(rules):
    light = rules.light
    assert P.is_bright(400, 10, light) and not P.is_bright(100, 250, light)
    assert P.is_bright(None, 200, light) and not P.is_bright(None, 100, light)
    assert P.is_dim(30, 250, light) and not P.is_dim(100, 10, light)
    assert P.is_dim(None, 30, light) and not P.is_dim(None, 100, light)
    assert not P.is_bright(None, None, light) and not P.is_dim(None, None, light)
