"""The Protocols habit rules (pure functions, made-up numbers)."""

import time

import pytest

from desktalk.habits import protocols as P
from desktalk.habits.sun_times import sun_times
from desktalk.util import at_time_today

MIN = 60


@pytest.fixture
def cfg(settings):
    return settings.protocols


def day_state(now, **kw):
    wake, bed = P.day_frame(now, '07:00', '22:30')
    s = {'at_desk': True, 'session_start': wake, 'wake_ts': wake, 'bed_ts': bed, 'sunset_ts': at_time_today('18:30', now)}
    s.update(kw)
    return s


def at(hhmm):
    """A timestamp for today at hh:mm (the rules only care about the time of day)."""
    return at_time_today(hhmm, time.time())


def keys(nudges):
    return [n.key for n in nudges]


def test_day_frame_handles_bedtime_after_midnight():
    now = at('00:20')
    wake, bed = P.day_frame(now, '08:00', '00:30')
    assert wake < now < bed                    # 00:20 still belongs to yesterday's waking day
    assert bed - wake == pytest.approx(16.5 * 3600)


def test_hydration_morning_and_pace(cfg):
    s = day_state(at('07:20'), water_ml=0)
    assert 'hydration.morning' in keys(P.hydration_nudges(s, cfg.hydration, at('07:20')))
    # 13:00: 6 h after waking, pace = 1.44 L; only 0.4 L, last sip 50 minutes ago
    now = at('13:00')
    s = day_state(now, water_ml=400, last_sip_ts=now - 50 * MIN)
    n = P.hydration_nudges(s, cfg.hydration, now)
    assert keys(n) == ['hydration.pace']
    assert '50 minutes' in n[0].evidence and '1.4 L' in n[0].evidence
    # on pace: nothing to say
    s = day_state(now, water_ml=1500, last_sip_ts=now - 50 * MIN)
    assert P.hydration_nudges(s, cfg.hydration, now) == []
    # recent sip: nothing to say
    s = day_state(now, water_ml=400, last_sip_ts=now - 10 * MIN)
    assert P.hydration_nudges(s, cfg.hydration, now) == []


def test_hydration_stops_two_hours_before_bed(cfg):
    now = at('21:00')                       # bed at 22:30
    s = day_state(now, water_ml=0, last_sip_ts=now - 3 * 3600)
    assert P.hydration_nudges(s, cfg.hydration, now) == []


def test_hydration_cup_on_desk_wording(cfg):
    now = at('12:00')
    s = day_state(now, water_ml=0, last_sip_ts=now - 60 * MIN, drink_on_desk=True)
    assert 'Nobody has picked me up' in P.hydration_nudges(s, cfg.hydration, now)[0].text


def test_breaks_after_30_minutes_sitting(cfg):
    now = at('10:00')
    assert P.break_nudges(day_state(now, sit_since=now - 25 * MIN), cfg.breaks, now) == []
    n = P.break_nudges(day_state(now, sit_since=now - 34 * MIN), cfg.breaks, now)
    assert keys(n) == ['breaks.sit'] and '34 minutes' in n[0].text and 'phone' in n[0].text
    assert n[0].repeat_after_sec == 15 * MIN
    # not at the desk: no nudge
    assert P.break_nudges(day_state(now, at_desk=False, sit_since=now - 40 * MIN), cfg.breaks, now) == []


def test_walk_after_a_meal(cfg):
    now = at('12:30')
    n = P.break_nudges(day_state(now, meal_done_ts=now - 2 * MIN, meal_name='sandwich'), cfg.breaks, now)
    assert n and n[0].key.startswith('breaks.meal') and 'sandwich' in n[0].text


def test_morning_sunlight(cfg):
    now = at('07:25')
    n = P.light_nudges(day_state(now), cfg.light, now)
    assert 'light.morning' in keys(n)
    assert '10 min' in n[0].text and 'window' in n[0].text
    # already logged → nothing
    assert 'light.morning' not in keys(P.light_nudges(day_state(now, sunlight_ts=now - 600), cfg.light, now))
    # 11:00 is too late for the morning rule
    assert 'light.morning' not in keys(P.light_nudges(day_state(at('11:00')), cfg.light, at('11:00')))


def test_afternoon_light_before_sunset(cfg):
    now = at('17:30')                     # sunset 18:30
    assert 'light.afternoon' in keys(P.light_nudges(day_state(now), cfg.light, now))
    assert 'light.afternoon' not in keys(P.light_nudges(day_state(at('15:00')), cfg.light, at('15:00')))


def test_evening_bright_room(cfg):
    now = at('21:45')
    bright = day_state(now, lux=400, bright_since=now - 10 * MIN)
    n = P.light_nudges(bright, cfg.light, now)
    assert keys(n) == ['light.evening'] and 'Bed is in 45 minutes' in n[0].text
    dim = day_state(now, lux=40, bright_since=None)
    assert P.light_nudges(dim, cfg.light, now) == []
    # cameras without lux use picture brightness
    level = day_state(now, brightness=200, bright_since=now - 10 * MIN)
    assert keys(P.light_nudges(level, cfg.light, now)) == ['light.evening']


def test_daytime_dim_room(cfg):
    now = at('11:00')
    n = P.light_nudges(day_state(now, sunlight_ts=now - 3600, lux=30, dim_since=now - 15 * MIN), cfg.light, now)
    assert keys(n) == ['light.daytime_dim']


def test_screens_off_an_hour_before_bed(cfg):
    now = at('21:50')
    n = P.screen_nudges(day_state(now, screen_on_desk=True), cfg.light, now)
    assert keys(n) == ['screens.bedtime'] and n[0].once_per_day
    assert P.screen_nudges(day_state(at('20:00'), screen_on_desk=True), cfg.light, at('20:00')) == []


def test_phone_on_desk(cfg):
    now = at('15:00')
    assert P.phone_nudges(day_state(now, phone_on_desk_since=now - 2 * MIN), cfg.phone_free, now) == []
    n = P.phone_nudges(day_state(now, phone_on_desk_since=now - 7 * MIN), cfg.phone_free, now)
    assert keys(n) == ['phone.focus'] and '7 minutes' in n[0].text
    # last hour before bed: the bedtime version
    late = at('22:00')
    assert keys(P.phone_nudges(day_state(late, phone_on_desk_since=late - 3 * MIN), cfg.phone_free, late)) == ['phone.bedtime']
    # phone is the camera: never
    assert P.phone_nudges(day_state(now, phone_on_desk_since=now - 30 * MIN, phone_counts=False), cfg.phone_free, now) == []


def test_original_food_and_task_reminders(cfg):
    now = at('14:00')
    assert P.food_nudges(day_state(now, food_since=now - 25 * MIN, food_name='donut'), cfg.food, now)[0].evidence \
        == 'A donut has been on the desk for 25 minutes.'
    assert P.task_nudges(day_state(now, open_todos=[]), cfg.tasks, now) == []
    assert P.task_nudges(day_state(now, open_todos=['Essay']), cfg.tasks, now)[0].text.startswith('You still have 1 open task')


def test_pick_respects_cooldowns_and_least_recent_habit():
    now = at('13:00')
    a = P.Nudge('hydration', 'hydration.pace', 'e', 't', repeat_after_sec=45 * MIN)
    b = P.Nudge('breaks', 'breaks.sit', 'e', 't', repeat_after_sec=15 * MIN)
    c = P.Nudge('light', 'light.morning', 'e', 't', once_per_day=True)
    assert P.pick_nudge([a, b], {}, now) in (a, b)
    last = {'breaks.sit': now - 5 * MIN, 'habit:breaks': now - 5 * MIN}
    assert P.pick_nudge([a, b], last, now) is a                       # breaks said 5 min ago
    last = {'habit:hydration': now - 60 * MIN, 'habit:breaks': now - 90 * MIN}
    assert P.pick_nudge([a, b], last, now) is b                       # least recently heard habit first
    assert P.pick_nudge([c], {'light.morning': now - 3 * 3600}, now) is None   # once a day


def test_candidate_nudges_runs_every_rule(cfg):
    now = at('13:00')
    s = day_state(now, water_ml=0, last_sip_ts=now - 2 * 3600, sit_since=now - 40 * MIN,
                  phone_on_desk_since=now - 10 * MIN, open_todos=['x'])
    habits = {n.habit for n in P.candidate_nudges(s, cfg, now)}
    assert {'hydration', 'breaks', 'phone_free', 'tasks'} <= habits


def test_sunset_for_los_angeles_in_october():
    import os
    old = os.environ.get('TZ')
    os.environ['TZ'] = 'America/Los_Angeles'
    time.tzset()
    try:
        now = time.mktime((2026, 10, 18, 12, 0, 0, 0, 0, -1))
        t = sun_times(now, 33.88, -118.40)
        sunset = time.localtime(t['sunset'])
        sunrise = time.localtime(t['sunrise'])
        assert (sunset.tm_hour, sunset.tm_min) >= (18, 5) and (sunset.tm_hour, sunset.tm_min) <= (18, 25)
        assert sunrise.tm_hour in (6, 7)
        assert sun_times(now, None, None, '18:30')['estimated']
    finally:
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()
