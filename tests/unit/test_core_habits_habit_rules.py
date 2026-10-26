"""The Protocols habit rules (desktalk/core/habits/habit_rules.py): pure functions, made-up numbers.

Times are on a fixed day (15 June 2026, local time) so the tests don't depend on today's date.
Wake 07:00, bed 22:30, sunset 18:30 unless a test says otherwise.
"""

import calendar
import re
import shutil
import time
from dataclasses import replace
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.habits import habit_rules as P
from desktalk.domain import Nudge, Todo
from desktalk.util import at_time_today
from tests.fakes import FakeClock

ROOT = Path(__file__).resolve().parents[2]
MIN = 60
HOUR = 3600
DAY = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))


CLOCK = FakeClock(utc_offset_hours=time.localtime(DAY).tm_gmtoff / 3600)   # the same local day as `at`


def project(root, **protocols):
    """A project folder with copies of the real config files; `protocols` changes lines of protocols.yaml."""
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    path = root / 'config' / 'protocols.yaml'
    text = path.read_text()
    for key, value in protocols.items():
        line = re.search(rf'^( *{key}:) *[^ #\n]+', text, re.M)
        assert line, key
        text = text[:line.start()] + f'{line.group(1)} {value}' + text[line.end():]
    path.write_text(text)
    return root


@pytest.fixture(scope='module')
def config(tmp_path_factory):
    """The config files as they are in the repo."""
    return load_config(project(tmp_path_factory.mktemp('project')), environ={})


@pytest.fixture(scope='module')
def rules(config):
    """config/protocols.yaml as it is in the repo, with the old hydration nudges switched back on (the
    hydration rule under rules: took over from them, but the functions are still here)."""
    protocols = config.protocols
    return protocols.model_copy(update={'hydration': changed(protocols.hydration, enabled=True)})


@pytest.fixture(scope='module')
def words(config):
    """The habits: wording in config/prompts.yaml, with the settings filled in."""
    return config.prompts.habits


def at(hhmm, days_later=0):
    return at_time_today(hhmm, DAY) + days_later * 86400


def day_state(now, **kw):
    wake, bed = P.day_frame(now, '07:00', '22:30')
    base = {'at_desk': True, 'session_start': wake, 'wake_ts': wake, 'bed_ts': bed, 'sunset_ts': at('18:30')}
    return P.RuleState(**{**base, **kw})


def keys(nudges):
    return [n.key for n in nudges]


def ready(nudges, last_fire, now):
    return P.ready_nudges(nudges, last_fire, now, day_of=CLOCK.local_day)


def changed(section, **values):
    """A copy of one protocols.yaml section with some settings changed."""
    return section.model_copy(update=values)


# ── the waking day ────────────────────────────────────────────────────
def test_day_frame_handles_bedtime_after_midnight():
    now = at('00:20')
    wake, bed = P.day_frame(now, '08:00', '00:30')
    assert wake < now < bed                    # 00:20 still belongs to yesterday's waking day
    assert bed - wake == pytest.approx(16.5 * HOUR)


def test_day_frame_on_the_day_the_clocks_go_back():
    """At 00:30 the clock is still on summer time (an hour less behind UTC), but 07:00 and 22:30 are on winter
    time: each is worked out with the offset at that time, not at `now`."""
    go_back = calendar.timegm((2026, 11, 1, 6, 0, 0, 0, 0, 0))       # New York: 02:00 summer time → 01:00

    def offset_at(ts):
        return -4 * HOUR if ts < go_back else -5 * HOUR

    now = calendar.timegm((2026, 11, 2, 4, 30, 0, 0, 0, 0))           # 23:30 winter time on 1 November
    wake, bed = P.day_frame(now, '07:00', '22:30', utc_offset_at=offset_at)
    assert wake == calendar.timegm((2026, 11, 1, 12, 0, 0, 0, 0, 0))  # 07:00 winter time
    assert bed == calendar.timegm((2026, 11, 2, 3, 30, 0, 0, 0, 0))   # 22:30 winter time


def europe_offset_at(change_utc, before_hours, after_hours):
    """Central Europe's offset from UTC around one clock change (summer time 2 hours ahead, winter time 1)."""
    def offset_at(ts):
        return before_hours * HOUR if ts < change_utc else after_hours * HOUR
    return offset_at


# The night summer time ends in Europe: 25 October 2026, 03:00 summer time → 02:00 winter time (01:00 UTC).
EUROPE_GO_BACK = europe_offset_at(calendar.timegm((2026, 10, 25, 1, 0, 0, 0, 0, 0)), 2, 1)
# The night summer time starts: 29 March 2026, 02:00 winter time → 03:00 summer time (01:00 UTC).
EUROPE_GO_FORWARD = europe_offset_at(calendar.timegm((2026, 3, 29, 1, 0, 0, 0, 0, 0)), 1, 2)


def test_day_frame_before_waking_on_the_night_the_clocks_go_back():
    """01:00 summer time on 25 October is still the waking day of the 24th: wake at 07:00 summer time on the
    24th, bed at 03:30 winter time on the 25th (not an hour off from stepping a fixed 24 hours)."""
    now = calendar.timegm((2026, 10, 24, 23, 0, 0, 0, 0, 0))            # 01:00 summer time on the 25th
    wake, bed = P.day_frame(now, '07:00', '03:30', utc_offset_at=EUROPE_GO_BACK)
    assert wake == calendar.timegm((2026, 10, 24, 5, 0, 0, 0, 0, 0))   # 07:00 summer time on the 24th
    assert bed == calendar.timegm((2026, 10, 25, 2, 30, 0, 0, 0, 0))   # 03:30 winter time on the 25th


def test_day_frame_before_waking_on_the_night_the_clocks_go_forward():
    now = calendar.timegm((2026, 3, 28, 23, 30, 0, 0, 0, 0))           # 00:30 winter time on 29 March
    wake, bed = P.day_frame(now, '07:00', '00:45', utc_offset_at=EUROPE_GO_FORWARD)
    assert wake == calendar.timegm((2026, 3, 28, 6, 0, 0, 0, 0, 0))    # 07:00 winter time on the 28th
    assert bed == calendar.timegm((2026, 3, 28, 23, 45, 0, 0, 0, 0))   # 00:45 winter time on the 29th


def test_day_frame_just_after_midnight_the_day_after_a_short_day():
    """The 29 March was only 23 hours long: going back a fixed 24 hours from 00:10 on the 30th lands on the
    28th, two days back. The waking day is still the 29th's."""
    now = calendar.timegm((2026, 3, 29, 22, 10, 0, 0, 0, 0))           # 00:10 summer time on 30 March
    wake, bed = P.day_frame(now, '07:00', '00:30', utc_offset_at=EUROPE_GO_FORWARD)
    assert wake == calendar.timegm((2026, 3, 29, 5, 0, 0, 0, 0, 0))    # 07:00 summer time on the 29th
    assert bed == calendar.timegm((2026, 3, 29, 22, 30, 0, 0, 0, 0))   # 00:30 summer time on the 30th


def test_bedtime_is_shown_in_its_own_local_time_on_the_night_the_clocks_go_back(rules, words):
    """Bed was at 01:30 summer time. At 02:15 winter time (45 minutes later) the nudge says 1:30, not 12:30."""
    now = calendar.timegm((2026, 10, 25, 1, 15, 0, 0, 0, 0))           # 02:15 winter time
    wake, bed = P.day_frame(now, '07:00', '01:30', utc_offset_at=EUROPE_GO_BACK)
    assert bed == calendar.timegm((2026, 10, 24, 23, 30, 0, 0, 0, 0))  # 01:30 summer time
    s = P.RuleState(wake_ts=wake, bed_ts=bed, at_desk=True, phone_on_desk_since=now - 10 * MIN,
                    utc_offset_sec=EUROPE_GO_BACK(now), utc_offset_at=EUROPE_GO_BACK)
    n = P.phone_nudges(s, rules.phone_free, now, words)
    assert keys(n) == ['phone.bedtime'] and 'Bed is at 1:30 AM' in n[0].evidence


def test_day_frame_before_waking_is_still_yesterday():
    wake, bed = P.day_frame(at('05:00'), '07:00', '22:30')
    assert wake == at('07:00', days_later=-1) and bed == at('22:30', days_later=-1)


# ── 1. hydration ──────────────────────────────────────────────────────
def test_hydration_morning_and_pace(rules, words):
    s = day_state(at('07:20'), water_ml=0)
    assert 'hydration.morning' in keys(P.hydration_nudges(s, rules.hydration, at('07:20'), words))
    # 13:00: 6 h after waking, pace = 1.44 L; only 0.4 L, last sip 50 minutes ago
    now = at('13:00')
    n = P.hydration_nudges(day_state(now, water_ml=400, last_sip_ts=now - 50 * MIN), rules.hydration, now, words)
    assert keys(n) == ['hydration.pace']
    assert '50 minutes' in n[0].evidence and '1.4 L' in n[0].evidence
    assert n[0].repeat_after_sec == 45 * MIN
    # on pace: nothing to say
    on_pace = day_state(now, water_ml=1500, last_sip_ts=now - 50 * MIN)
    assert P.hydration_nudges(on_pace, rules.hydration, now, words) == []
    # recent sip: nothing to say
    recent = day_state(now, water_ml=400, last_sip_ts=now - 10 * MIN)
    assert P.hydration_nudges(recent, rules.hydration, now, words) == []


def test_hydration_morning_nudge_is_once_a_day(rules, words):
    n = P.hydration_nudges(day_state(at('07:20'), water_ml=0), rules.hydration, at('07:20'), words)
    assert n[0].once_per_day and n[0].speakers == ('hydration',)
    assert n[0].repeat_after_sec == 45 * MIN          # hydration.remind_after_min


def test_water_tip_follows_on_waking_ml(tmp_path):
    # enabled: the first one in protocols.yaml is hydration's (off since the hydration rule took over)
    config = load_config(project(tmp_path, on_waking_ml=450, enabled='true'), environ={})
    s = day_state(at('07:20'), water_ml=0)
    n = P.hydration_nudges(s, config.protocols.hydration, at('07:20'), config.prompts.habits)
    assert '450 ml' in n[0].tip and '600 ml' not in n[0].tip
    assert 'about 0.5 L' in n[0].text


def test_the_nudge_words_come_from_prompts_yaml(rules, words):
    now = at('13:00')
    mine = words.model_copy(update={'breaks_sit': words.breaks_sit.model_copy(update={
        'evidence': 'Sat {minutes} min.', 'text': 'Up you get after {minutes} min!{phone_note}', 'tip': 'Walk.'})})
    n = P.break_nudges(day_state(now, sit_since=now - 34 * MIN), rules.breaks, now, mine)
    assert (n[0].evidence, n[0].text, n[0].tip) == ('Sat 34 min.', f'Up you get after 34 min!{words.phone_note}',
                                                   'Walk.')


def test_hydration_stops_two_hours_before_bed(rules, words):
    now = at('21:00')                       # bed at 22:30
    s = day_state(now, water_ml=0, last_sip_ts=now - 3 * HOUR)
    assert P.hydration_nudges(s, rules.hydration, now, words) == []
    now = at('20:20')
    s = day_state(now, water_ml=0, last_sip_ts=now - 3 * HOUR)
    assert keys(P.hydration_nudges(s, rules.hydration, now, words)) == ['hydration.pace']


def test_hydration_quiet_time_counts_from_when_the_camera_started(rules, words):
    now = at('13:00')
    s = day_state(now, water_ml=0, session_start=now - 20 * MIN)       # no sip yet, but we only just started
    assert P.hydration_nudges(s, rules.hydration, now, words) == []


def test_hydration_cup_on_desk_wording(rules, words):
    now = at('12:00')
    s = day_state(now, water_ml=0, last_sip_ts=now - 60 * MIN, drink_on_desk=True)
    assert 'Nobody has picked me up' in P.hydration_nudges(s, rules.hydration, now, words)[0].text
    s = replace(s, drink_on_desk=False)
    assert "I haven't seen you drink anything" in P.hydration_nudges(s, rules.hydration, now, words)[0].text


def test_hydration_waits_while_nobody_is_at_the_desk(rules, words):
    now = at('13:00')
    s = day_state(now, at_desk=False, water_ml=0, last_sip_ts=now - 3 * HOUR)
    assert P.hydration_nudges(s, rules.hydration, now, words) == []


def test_water_on_waking_window_comes_from_protocols_yaml(rules, words):
    now = at('07:45')
    s = day_state(now, water_ml=0)
    assert 'hydration.morning' in keys(P.hydration_nudges(s, rules.hydration, now, words))
    shorter = changed(rules.hydration, on_waking_window_min=30)
    assert 'hydration.morning' not in keys(P.hydration_nudges(s, shorter, now, words))


def test_water_on_waking_done_share_comes_from_protocols_yaml(rules, words):
    now = at('07:20')
    s = day_state(now, water_ml=400)                       # 400 of the 600 ml on waking
    assert 'hydration.morning' not in keys(P.hydration_nudges(s, rules.hydration, now, words))      # half is done
    stricter = changed(rules.hydration, on_waking_done_share=0.75)
    assert 'hydration.morning' in keys(P.hydration_nudges(s, stricter, now, words))


# ── 2. breaks ─────────────────────────────────────────────────────────
def test_breaks_after_30_minutes_sitting(rules, words):
    now = at('10:00')
    assert P.break_nudges(day_state(now, sit_since=now - 25 * MIN), rules.breaks, now, words) == []
    n = P.break_nudges(day_state(now, sit_since=now - 34 * MIN), rules.breaks, now, words)
    assert keys(n) == ['breaks.sit'] and '34 minutes' in n[0].text and 'phone' in n[0].text
    assert n[0].repeat_after_sec == 15 * MIN and n[0].speakers == ('breaks', 'screens')
    # not at the desk: no nudge
    assert P.break_nudges(day_state(now, at_desk=False, sit_since=now - 40 * MIN), rules.breaks, now, words) == []


def test_break_nudge_leaves_out_the_phone_note_when_switched_off(rules, words):
    now = at('10:00')
    no_note = changed(rules.breaks, no_phone_on_breaks=False)
    assert 'phone' not in P.break_nudges(day_state(now, sit_since=now - 34 * MIN), no_note, now, words)[0].text


def test_walk_after_a_meal(rules, words):
    now = at('12:30')
    n = P.break_nudges(day_state(now, meal_done_ts=now - 2 * MIN, meal_name='sandwich'), rules.breaks, now, words)
    assert n and n[0].key == f'breaks.meal.{int(now - 2 * MIN)}' and 'sandwich' in n[0].text
    assert n[0].speakers == ('breaks', 'food') and n[0].repeat_after_sec == 6 * HOUR
    away = day_state(now, at_desk=False, meal_done_ts=now - 2 * MIN, meal_name='sandwich')
    assert P.break_nudges(away, rules.breaks, now, words) == []
    off = changed(rules.breaks, walk_after_meal=False)
    assert P.break_nudges(day_state(now, meal_done_ts=now - 2 * MIN, meal_name='sandwich'), off, now, words) == []


def test_walk_window_after_a_meal_comes_from_protocols_yaml(rules, words):
    now = at('12:30')
    s = day_state(now, meal_done_ts=now - 12 * MIN, meal_name='sandwich')
    assert P.break_nudges(s, rules.breaks, now, words) == []                   # 12 min > 10 min window
    longer = changed(rules.breaks, walk_window_after_meal_min=15)
    assert keys(P.break_nudges(s, longer, now, words)) == [f'breaks.meal.{int(now - 12 * MIN)}']


def test_walk_repeat_comes_from_protocols_yaml(rules, words):
    now = at('12:30')
    s = day_state(now, meal_done_ts=now - 2 * MIN)
    n = P.break_nudges(s, changed(rules.breaks, walk_repeat_after_hours=2), now, words)
    assert n[0].repeat_after_sec == 2 * HOUR


# ── 3. light ──────────────────────────────────────────────────────────
def test_morning_sunlight(rules, words):
    now = at('07:25')
    n = P.light_nudges(day_state(now), rules.light, now, words)
    assert 'light.morning' in keys(n)
    assert '10 min' in n[0].text and 'window' in n[0].text
    assert n[0].once_per_day and n[0].repeat_after_sec == 60 * MIN
    # already logged → nothing
    assert 'light.morning' not in keys(P.light_nudges(day_state(now, sunlight_ts=now - 600), rules.light, now, words))
    # 11:00 is too late for the morning rule
    assert 'light.morning' not in keys(P.light_nudges(day_state(at('11:00')), rules.light, at('11:00'), words))


def test_morning_sunlight_grace_comes_from_protocols_yaml(rules, words):
    now = at('08:25')                        # 85 minutes after waking: 60 min window + 30 min grace
    assert 'light.morning' in keys(P.light_nudges(day_state(now), rules.light, now, words))
    assert 'light.morning' not in keys(P.light_nudges(day_state(at('08:35')), rules.light, at('08:35'), words))
    no_grace = changed(rules.light, morning_grace_min=0)
    assert 'light.morning' not in keys(P.light_nudges(day_state(now), no_grace, now, words))


def test_afternoon_light_before_sunset(rules, words):
    now = at('17:30')                     # sunset 18:30
    n = P.light_nudges(day_state(now), rules.light, now, words)
    assert keys(n) == ['light.afternoon'] and n[0].once_per_day
    assert 'light.afternoon' not in keys(P.light_nudges(day_state(at('15:00')), rules.light, at('15:00'), words))
    assert P.light_nudges(day_state(now, afternoon_light_ts=now - 600), rules.light, now, words) == []
    assert P.light_nudges(day_state(now), changed(rules.light, afternoon=False), now, words) == []


def test_sunset_guess_without_a_sunset_time_comes_from_protocols_yaml(rules, words):
    now = at('17:30')
    s = day_state(now, sunset_ts=None)       # guessed: 4 hours before the 22:30 bedtime = 18:30
    assert keys(P.light_nudges(s, rules.light, now, words)) == ['light.afternoon']
    later_guess = changed(rules.light, sunset_guess_before_bed_hours=2)      # 20:30: too early for it
    assert P.light_nudges(s, later_guess, now, words) == []


def test_evening_bright_room(rules, words):
    now = at('21:45')
    bright = day_state(now, lux=400, bright_since=now - 10 * MIN)
    n = P.light_nudges(bright, rules.light, now, words)
    assert keys(n) == ['light.evening'] and 'Bed is in 45 minutes' in n[0].text
    assert n[0].repeat_after_sec == 60 * MIN
    dim = day_state(now, lux=40, bright_since=None)
    assert P.light_nudges(dim, rules.light, now, words) == []
    # cameras without lux use picture brightness
    level = day_state(now, brightness=200, bright_since=now - 10 * MIN)
    assert keys(P.light_nudges(level, rules.light, now, words)) == ['light.evening']


def test_evening_after_sunset_but_long_before_bed(rules, words):
    now = at('19:00')
    n = P.light_nudges(day_state(now, lux=400, bright_since=now - 10 * MIN), rules.light, now, words)
    assert keys(n) == ['light.evening'] and n[0].text.startswith('The sun has set')


def test_evening_bright_wait_comes_from_protocols_yaml(rules, words, tmp_path):
    now = at('21:45')
    s = day_state(now, lux=400, bright_since=now - 4 * MIN)
    assert P.light_nudges(s, rules.light, now, words) == []                     # 4 of 5 minutes
    sooner = load_config(project(tmp_path, evening_bright_min=3), environ={})   # the words say 3 minutes too
    n = P.light_nudges(s, sooner.protocols.light, now, sooner.prompts.habits)
    assert keys(n) == ['light.evening'] and 'over 3 minutes' in n[0].evidence


def test_bed_soon_wording_comes_from_protocols_yaml(rules, words):
    now = at('21:45')
    s = day_state(now, lux=400, bright_since=now - 10 * MIN)
    late_only = changed(rules.light, bed_soon_min=30)
    assert P.light_nudges(s, late_only, now, words)[0].text.startswith('The sun has set')


def test_daytime_dim_room(rules, words):
    now = at('11:00')
    s = day_state(now, sunlight_ts=now - HOUR, lux=30, dim_since=now - 15 * MIN)
    n = P.light_nudges(s, rules.light, now, words)
    assert keys(n) == ['light.daytime_dim'] and n[0].repeat_after_sec == 60 * MIN
    # not dim for long enough yet
    assert P.light_nudges(day_state(now, sunlight_ts=now - HOUR, lux=30, dim_since=now - 5 * MIN), rules.light,
                          now, words) == []
    # cameras without lux use picture brightness
    s = day_state(now, sunlight_ts=now - HOUR, brightness=30, dim_since=now - 15 * MIN)
    assert keys(P.light_nudges(s, rules.light, now, words)) == ['light.daytime_dim']


def test_daytime_dim_margins_come_from_protocols_yaml(rules, words):
    early = at('07:50')                      # 50 minutes after waking
    s = day_state(early, sunlight_ts=early - 600, lux=30, dim_since=early - 15 * MIN)
    assert P.light_nudges(s, rules.light, early, words) == []
    late = at('17:45')                       # 45 minutes before sunset
    t = day_state(late, afternoon_light_ts=late - 600, lux=30, dim_since=late - 15 * MIN)
    assert P.light_nudges(t, rules.light, late, words) == []
    smaller = changed(rules.light, day_dim_margin_min=30)
    assert keys(P.light_nudges(s, smaller, early, words)) == ['light.daytime_dim']
    assert keys(P.light_nudges(t, smaller, late, words)) == ['light.daytime_dim']


def test_light_rules_wait_while_nobody_is_at_the_desk(rules, words):
    now = at('07:25')
    assert P.light_nudges(day_state(now, at_desk=False), rules.light, now, words) == []


def test_screens_off_an_hour_before_bed(rules, words):
    now = at('21:50')
    n = P.screen_nudges(day_state(now, screen_on_desk=True), rules.light, now, words)
    assert keys(n) == ['screens.bedtime'] and n[0].once_per_day and n[0].speakers == ('screens',)
    assert n[0].repeat_after_sec == 60 * MIN
    assert 'Bed is at' in n[0].evidence
    assert P.screen_nudges(day_state(at('20:00'), screen_on_desk=True), rules.light, at('20:00'), words) == []
    assert P.screen_nudges(day_state(now, screen_on_desk=False), rules.light, now, words) == []


def test_screens_after_bedtime_until_the_limit_from_protocols_yaml(rules, words):
    now = at('01:00', days_later=1)          # 2.5 hours after the 22:30 bedtime
    s = day_state(now, screen_on_desk=True)
    n = P.screen_nudges(s, rules.light, now, words)
    assert keys(n) == ['screens.bedtime'] and n[0].evidence.startswith('It is past your bedtime')
    sooner = changed(rules.light, screens_until_after_bed_hours=2)
    assert P.screen_nudges(s, sooner, now, words) == []


# ── 4. phone-free time ────────────────────────────────────────────────
def test_phone_on_desk(rules, words):
    now = at('15:00')
    assert P.phone_nudges(day_state(now, phone_on_desk_since=now - 2 * MIN), rules.phone_free, now, words) == []
    n = P.phone_nudges(day_state(now, phone_on_desk_since=now - 7 * MIN), rules.phone_free, now, words)
    assert keys(n) == ['phone.focus'] and '7 minutes' in n[0].text
    assert n[0].repeat_after_sec == 25 * MIN and n[0].speakers == ('phone_free', 'tasks')
    # last hour before bed: the bedtime version
    late = at('22:00')
    n = P.phone_nudges(day_state(late, phone_on_desk_since=late - 3 * MIN), rules.phone_free, late, words)
    assert keys(n) == ['phone.bedtime'] and n[0].once_per_day and n[0].repeat_after_sec == 25 * MIN
    # phone is the camera: never
    s = day_state(now, phone_on_desk_since=now - 30 * MIN, phone_counts=False)
    assert P.phone_nudges(s, rules.phone_free, now, words) == []


def test_phone_before_bed_wait_comes_from_protocols_yaml(rules, words):
    late = at('22:00')
    s = day_state(late, phone_on_desk_since=late - 3 * MIN)
    longer = changed(rules.phone_free, before_bed_on_desk_min=5)
    assert P.phone_nudges(s, longer, late, words) == []          # 3 of 5 minutes, and too soon for the focus nudge


def test_phone_bedtime_nudge_stops_after_bed_as_set_in_protocols_yaml(rules, words):
    now = at('00:30', days_later=1)          # 2 hours after the 22:30 bedtime
    s = day_state(now, phone_on_desk_since=now - 10 * MIN)
    assert keys(P.phone_nudges(s, rules.phone_free, now, words)) == ['phone.bedtime']
    sooner = changed(rules.phone_free, before_bed_until_after_bed_hours=1)
    assert keys(P.phone_nudges(s, sooner, now, words)) == ['phone.focus']


# ── kept from the original reminders ──────────────────────────────────
def test_original_food_and_task_reminders(rules, words):
    now = at('14:00')
    n = P.food_nudges(day_state(now, food_since=now - 25 * MIN, food_name='donut'), rules.food, now, words)
    assert n[0].evidence == 'A donut has been on the desk for 25 minutes.'
    assert n[0].key == f'food.sitting.{int(now - 25 * MIN)}' and n[0].repeat_after_sec == 6 * HOUR
    assert P.food_nudges(day_state(now, food_since=now - 15 * MIN, food_name='donut'), rules.food, now, words) == []
    assert P.task_nudges(day_state(now, open_todos=()), rules.tasks, now, words) == []
    n = P.task_nudges(day_state(now, open_todos=(Todo('Essay'),)), rules.tasks, now, words)
    assert n[0].text.startswith('How about "Essay"') and n[0].repeat_after_sec == 120 * MIN
    two = P.task_nudges(day_state(now, open_todos=(Todo('Essay'), Todo(''), Todo('Email'))), rules.tasks, now,
                        words)
    assert two[0].text.startswith('How about "Essay"')


def test_food_repeat_comes_from_protocols_yaml(rules, words):
    now = at('14:00')
    s = day_state(now, food_since=now - 25 * MIN, food_name='donut')
    assert P.food_nudges(s, changed(rules.food, repeat_after_hours=2), now, words)[0].repeat_after_sec == 2 * HOUR


def test_how_much_of_a_task_name_is_shown_comes_from_protocols_yaml(rules, words):
    now = at('14:00')
    s = day_state(now, open_todos=(Todo('Write the history essay about the river towns'),))
    n = P.task_nudges(s, changed(rules.tasks, name_shown_chars=9), now, words)
    assert '"Write the"' in n[0].text and '"Write the h' not in n[0].text


def test_task_check_in_waits_until_the_camera_has_watched_long_enough(rules, words):
    now = at('14:00')
    s = day_state(now, open_todos=(Todo('Essay'),), session_start=now - 30 * MIN)
    assert P.task_nudges(s, rules.tasks, now, words) == []
    away = day_state(now, open_todos=(Todo('Essay'),), at_desk=False)
    assert P.task_nudges(away, rules.tasks, now, words) == []


# ── to-dos with due dates, and the check-in without one ────────────
def task_state(now, *todos, **kw):
    """Today's state with these open to-dos, the local day filled in as the tracker does (CLOCK.local_day)."""
    return day_state(now, open_todos=tuple(todos), today=CLOCK.local_day(now), **kw)


def run_days(rules, words, days, *todos, every=10 * MIN, start='09:00', end='21:00'):
    """Ask the to-do rule every `every` while at the desk from `start` to `end`, each day, and send what's ready
    (as the reminders do). Returns the nudges sent."""
    sent, last_fire = [], {}
    for day in range(days):
        now = at(start, day)
        while now <= at(end, day):
            for n in ready(P.task_nudges(task_state(now, *todos), rules.tasks, now, words), last_fire, now):
                sent.append((CLOCK.local_day(now), n))
                last_fire[n.key] = now
                last_fire[f'habit:{n.habit}'] = now
            now += every
    return sent


def test_due_tomorrow_reminds_once(rules, words):
    tomorrow = CLOCK.local_day(at('12:00', 1))
    sent = run_days(rules, words, 1, Todo('essay', due=tomorrow))
    due = [n for _, n in sent if n.key.startswith('tasks.due.')]
    assert [n.key for n in due] == ['tasks.due.essay.tomorrow']
    assert due[0].text == 'Remember, your essay is due tomorrow.'
    assert due[0].evidence == '"essay" on the user\'s to-do list is due tomorrow.'
    assert due[0].habit == 'tasks' and due[0].repeat_after_sec == 24 * HOUR


def test_due_today_reminds(rules, words):
    now = at('14:00')
    n = P.task_nudges(task_state(now, Todo('essay', due=CLOCK.local_day(now))), rules.tasks, now, words)
    assert [(x.key, x.text, x.repeat_after_sec) for x in n] == [
        ('tasks.due.essay.today', 'Your essay is due today.', 24 * HOUR)]


def test_due_in_a_few_days_says_nothing_yet(rules, words):
    now = at('14:00')
    later = CLOCK.local_day(at('12:00', 3))
    assert P.task_nudges(task_state(now, Todo('essay', due=later)), rules.tasks, now, words) == []


def test_late_reminds_daily(rules, words):
    two_days_ago = CLOCK.local_day(at('12:00', -2))
    sent = run_days(rules, words, 3, Todo('essay', due=two_days_ago))
    late = [(day, n) for day, n in sent if n.key.startswith('tasks.due.essay.late.')]
    assert len(late) == 3 and len({day for day, _ in late}) == 3           # one a day
    assert [n.text for _, n in late] == ['Your essay was due 2 days ago. Done? Tick it off.',
                                         'Your essay was due 3 days ago. Done? Tick it off.',
                                         'Your essay was due 4 days ago. Done? Tick it off.']
    assert late[0][1].repeat_after_sec == rules.tasks.late_repeat_hours * HOUR


def test_late_is_said_every_day_even_when_yesterdays_was_late_in_the_evening(rules, words):
    """Said at 21:00 one day, and you leave at 20:00 the next: that day still gets its reminder."""
    essay = Todo('essay', due=CLOCK.local_day(at('12:00', -2)))
    last_fire: dict[str, float] = {}
    sent = []
    for now in (at('21:00'), *(at('09:00', 1) + k * 10 * MIN for k in range(67))):     # 09:00 to 20:00 next day
        for n in ready(P.task_nudges(task_state(now, essay), rules.tasks, now, words), last_fire, now):
            sent.append(CLOCK.local_day(now))
            last_fire[n.key] = now
    assert sent == [CLOCK.local_day(at('21:00')), CLOCK.local_day(at('09:00', 1))]


def test_one_day_late_has_no_plural(rules, words):
    now = at('14:00')
    yesterday = CLOCK.local_day(at('12:00', -1))
    n = P.task_nudges(task_state(now, Todo('essay', due=yesterday)), rules.tasks, now, words)
    assert n[0].text == 'Your essay was due 1 day ago. Done? Tick it off.'


def test_due_soon_days_comes_from_protocols_yaml(rules, words):
    """With due_soon_days 3 the reminders start three days before: "in 3 days", "in 2 days", then "tomorrow"
    the day before, each said once that day."""
    in_three = CLOCK.local_day(at('12:00', 3))
    three_days = rules.model_copy(update={'tasks': changed(rules.tasks, due_soon_days=3)})
    sent = run_days(three_days, words, 3, Todo('essay', due=in_three))
    due = [(day, n) for day, n in sent if n.key.startswith('tasks.due.')]
    assert [day for day, _ in due] == [CLOCK.local_day(at('12:00', k)) for k in range(3)]       # one a day
    assert [n.key for _, n in due] == ['tasks.due.essay.soon.3', 'tasks.due.essay.soon.2', 'tasks.due.essay.tomorrow']
    assert [n.text for _, n in due] == ['Heads up: your essay is due in 3 days.',
                                        'Heads up: your essay is due in 2 days.',
                                        'Remember, your essay is due tomorrow.']
    assert due[0][1].evidence == '"essay" on the user\'s to-do list is due in 3 days.'


def test_done_todo_no_reminder(rules, words):
    now = at('14:00')
    today = CLOCK.local_day(now)
    s = task_state(now, Todo('essay', done=True, due=today), Todo('email', done=True))
    assert P.task_nudges(s, rules.tasks, now, words) == []


def test_due_reminders_wait_while_nobody_is_at_the_desk(rules, words):
    now = at('14:00')
    s = task_state(now, Todo('essay', due=CLOCK.local_day(now)), at_desk=False)
    assert P.task_nudges(s, rules.tasks, now, words) == []


def test_a_bad_due_date_counts_as_none(rules, words):
    now = at('14:00')
    n = P.task_nudges(task_state(now, Todo('essay', due='someday')), rules.tasks, now, words)
    assert [x.key for x in n] == ['tasks.open']


def test_check_in_every_two_hours(rules, words):
    start = at('09:00')
    s = task_state(start + 119 * MIN, Todo('essay'), session_start=start)
    assert P.task_nudges(s, rules.tasks, start + 119 * MIN, words) == []
    s = task_state(start + 120 * MIN, Todo('essay'), session_start=start)
    n = P.task_nudges(s, rules.tasks, start + 120 * MIN, words)
    assert [(x.key, x.repeat_after_sec) for x in n] == [('tasks.open', 120 * MIN)]


def test_no_due_rotates_names(rules, words):
    now = at('14:00')
    todos = (Todo('a'), Todo('b'), Todo('c'))
    named = [P.task_nudges(task_state(now, *todos, task_turn=turn), rules.tasks, now, words)[0].text
             for turn in range(4)]
    assert [t.split('"')[1] for t in named] == ['a', 'b', 'c', 'a']


def test_the_check_in_names_only_to_dos_without_a_due_date(rules, words):
    now = at('14:00')
    later = CLOCK.local_day(at('12:00', 5))
    n = P.task_nudges(task_state(now, Todo('essay', due=later), Todo('email')), rules.tasks, now, words)
    assert [x.text.split('"')[1] for x in n] == ['email']
    assert P.task_nudges(task_state(now, Todo('essay', due=later)), rules.tasks, now, words) == []


@pytest.mark.parametrize('section, rule, extra', [
    ('hydration', P.hydration_nudges, {'water_ml': 0, 'last_sip_ts': at('10:00')}),
    ('breaks', P.break_nudges, {'sit_since': at('12:00')}),
    ('light', P.light_nudges, {'lux': 30, 'dim_since': at('12:00')}),
    ('light', P.screen_nudges, {'screen_on_desk': True}),
    ('phone_free', P.phone_nudges, {'phone_on_desk_since': at('12:00')}),
    ('food', P.food_nudges, {'food_since': at('12:00'), 'food_name': 'donut'}),
    ('tasks', P.task_nudges, {'open_todos': (Todo('Essay'),)}),
])
def test_a_switched_off_habit_never_nudges(rules, words, section, rule, extra):
    now = at('13:00') if rule is not P.screen_nudges else at('22:00')
    s = day_state(now, **extra)
    assert rule(s, getattr(rules, section), now, words)            # on: there is something to say
    assert rule(s, changed(getattr(rules, section), enabled=False), now, words) == []


# ── choosing which one to send ────────────────────────────────────────
def test_ready_nudges_respect_cooldowns_and_least_recent_habit():
    now = at('13:00')
    a = Nudge('hydration', 'hydration.pace', 'e', 't', repeat_after_sec=45 * MIN)
    b = Nudge('breaks', 'breaks.sit', 'e', 't', repeat_after_sec=15 * MIN)
    c = Nudge('light', 'light.morning', 'e', 't', repeat_after_sec=60 * MIN, once_per_day=True)
    assert ready([a, b], {}, now)[0] is a                         # nothing said yet: the first one
    last = {'breaks.sit': now - 5 * MIN, 'habit:breaks': now - 5 * MIN}
    assert ready([a, b], last, now)[0] is a                       # breaks said 5 min ago
    last = {'habit:hydration': now - 60 * MIN, 'habit:breaks': now - 90 * MIN}
    assert ready([a, b], last, now)[0] is b                       # least recently heard habit first
    assert ready([c], {'light.morning': now - 3 * HOUR}, now) == []   # once a day
    assert ready([c], {'light.morning': now - 24 * HOUR}, now)[0] is c     # yesterday's doesn't count
    assert ready([], {}, now) == []


def test_ready_nudges_are_in_the_order_to_send_them():
    now = at('13:00')
    a = Nudge('hydration', 'hydration.pace', 'e', 't', repeat_after_sec=45 * MIN)
    b = Nudge('breaks', 'breaks.sit', 'e', 't', repeat_after_sec=15 * MIN)
    c = Nudge('food', 'food.sitting.1', 'e', 't', repeat_after_sec=6 * HOUR)
    last = {'habit:hydration': now - 10 * MIN, 'habit:breaks': now - 90 * MIN,
            'food.sitting.1': now - HOUR, 'habit:food': now - HOUR}
    assert ready([a, b, c], last, now) == [b, a]


def test_a_once_a_day_nudge_also_waits_its_repeat_time_after_midnight():
    c = Nudge('screens', 'screens.bedtime', 'e', 't', repeat_after_sec=60 * MIN, once_per_day=True)
    fired = at('23:50')
    assert ready([c], {'screens.bedtime': fired}, at('00:20', days_later=1)) == []
    assert ready([c], {'screens.bedtime': fired}, at('00:55', days_later=1))[0] is c


def test_candidate_nudges_runs_every_rule(rules, words):
    now = at('13:00')
    s = day_state(now, water_ml=0, last_sip_ts=now - 2 * HOUR, sit_since=now - 40 * MIN,
                  phone_on_desk_since=now - 10 * MIN, open_todos=(Todo('x'),), food_since=now - HOUR,
                  food_name='donut', lux=30, dim_since=now - HOUR)
    habits = {n.habit for n in P.candidate_nudges(s, rules, now, words)}
    assert habits == {'hydration', 'breaks', 'light', 'phone_free', 'food', 'tasks'}
    late = at('22:00')
    s = day_state(late, screen_on_desk=True)
    assert 'screens' in {n.habit for n in P.candidate_nudges(s, rules, late, words)}


def test_bright_and_dim_prefer_lux_over_picture_brightness(rules):
    light = rules.light
    assert P.is_bright(400, 10, light) and not P.is_bright(100, 250, light)
    assert P.is_bright(None, 200, light) and not P.is_bright(None, 100, light)
    assert P.is_dim(30, 250, light) and not P.is_dim(100, 10, light)
    assert P.is_dim(None, 30, light) and not P.is_dim(None, 100, light)
    assert not P.is_bright(None, None, light) and not P.is_dim(None, None, light)


def test_the_waking_day_and_the_times_in_the_words_follow_the_utc_offset(rules, words):
    offset = time.localtime(DAY).tm_gmtoff + HOUR          # an hour ahead of this computer
    now = at('17:30')
    wake, bed = P.day_frame(now, '07:00', '22:30', utc_offset_at=lambda ts: offset)
    assert (wake, bed) == (at('07:00') - HOUR, at('22:30') - HOUR)
    s = day_state(now, utc_offset_sec=offset)
    n = P.light_nudges(s, rules.light, now, words)          # sunset at 18:30 here is 19:30 there
    assert keys(n) == ['light.afternoon'] and '7:30 PM' in n[0].text + n[0].evidence
