"""The data a weekly report is written from (desktalk_cloud.report_data.build_week), built from stored events."""

from __future__ import annotations

import datetime as dt
from datetime import UTC, datetime

import pytest

from desktalk_cloud.config import ConfigError
from desktalk_cloud.domain import Device, ObjectState
from desktalk_cloud.report_data import DATA_WORD_KEYS, build_week, load_report_words, next_sign, with_data
from desktalk_cloud.report_times import week_of, zone_of
from tests.fakes.cloud import cloud_config

CONFIG = cloud_config()
WORDS = load_report_words(CONFIG.prompts, CONFIG.messages)
DATA_WORDS = WORDS.data
UTC_ZONE = zone_of('UTC')
DUE = datetime(2026, 11, 1, 20, tzinfo=UTC).timestamp()          # Sunday 1 Nov, 20:00
WEEK = week_of(DUE, UTC_ZONE)                                    # Sun 25 Oct 20:00 to Sun 1 Nov 20:00
START, END, FIRST_DAY = WEEK
WRITTEN = DUE + 600
EVERY_DAY = [(dt.date(2026, 10, 26) + dt.timedelta(days=i)).isoformat() for i in range(7)]


def at(day: int, hour: int, minute: int = 0, second: int = 0, month: int = 10) -> float:
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC).timestamp()


def device(**more) -> Device:
    base = {'id': 'd1', 'user_id': 'u1', 'name': 'My Pi', 'time_zone': 'UTC', 'created_at': START - 1000,
            'last_seen_at': END + 60, 'caught_up_at': END + 60}
    return Device(**{**base, **more})


def totals_event(day: str, **numbers) -> dict:
    totals = {'desk_sec': 3600, 'water_ml': 800, 'sips': 10, 'breaks': 2, 'longest_sit_sec': 1800,
              'phone_free_sec': 3000, 'phone_on_desk_sec': 600, 'sunlight_minutes': 15, 'nudges_sent': 3}
    totals.update(numbers)
    return {'id': f'daily-{day}', 'ts': END, 'type': 'daily_totals', 'day': day, 'totals': totals}


def object_state(event_id, ts, change, name, **more) -> dict:
    return {'id': event_id, 'ts': ts, 'type': 'object_state', 'change': change, 'object': name,
            'prev_state': 'ABSENT', 'new_state': 'PRESENT', 'device_id': 'cam', **more}


def habit(event_id, ts, name, action, **more) -> dict:
    return {'id': event_id, 'ts': ts, 'type': 'habit', 'habit': name, 'action': action, **more}


SEATED = 'The user has been at the desk for 30 minutes.'


def nudge(event_id, ts, habit_name, rule, evidence: str | None = SEATED) -> dict:
    event = {'id': event_id, 'ts': ts, 'type': 'nudge', 'habit': habit_name, 'rule': rule, 'agent': 'cup'}
    if evidence is not None:
        event['evidence'] = evidence
    return event


GOALS = {'id': 'goals-1', 'ts': START, 'type': 'goals', 'wake': '07:00', 'bed': '22:30',
         'goals': {'water_ml': 2400, 'water_window_hours': 10, 'sit_limit_min': 30, 'break_min': 5,
                   'phone_free_min': 120, 'phone_away_before_bed_min': 60, 'morning_light_window_min': 60,
                   'evening_dim_before_bed_min': 60, 'screens_off_before_bed_min': 60}}


def build(events=(), totals=(), goals=GOALS, states=(), last_camera=None, dev=None, now=WRITTEN, week=WEEK):
    return build_week(dev or device(), list(events), list(totals), goals, list(states), last_camera, now, week,
                      DATA_WORDS)


# ── the whole picture ────────────────────────────────────────────────────────────────────────────────────────────
def test_has_coverage_goals_totals_nudges_snapshot_events():
    events = [
        nudge('n1', at(28, 17, 4), 'breaks', 'breaks.sit'),
        habit('h1', at(28, 17, 10), 'breaks', 'break', how='button', minutes=5),
        habit('h2', at(28, 9, 0), 'hydration', 'sip', how='camera', ml=80, total_ml=80, healthy=True),
        object_state('o1', at(28, 9, 5), 'appeared', 'cell phone', conf_avg=0.8123, dwell_sec=42.4),
        {'id': 'a1', 'ts': at(28, 9, 6), 'type': 'alert', 'kind': 'proximity', 'scenario': 'risk_detection'},
        {'id': 'c1', 'ts': at(28, 9, 7), 'type': 'camera', 'key': 'lost', 'kind': 'problem'},
    ]
    totals = [totals_event(day) for day in EVERY_DAY[:3]]
    states = [ObjectState(label='cell phone', state='PRESENT', since=at(28, 9, 5), updated_at=at(28, 9, 6)),
              ObjectState(label='laptop', state='ABSENT', since=at(27, 10), updated_at=at(27, 10))]
    camera = {'id': 'c1', 'ts': at(28, 9, 7), 'type': 'camera', 'key': 'lost', 'kind': 'problem'}
    text = build(events, totals, states=states, last_camera=camera)

    assert text.startswith('# Desk Talk data: Mon Oct 26 to Sun Nov 01, 2026')
    assert 'UTC' in text and 'Sun Nov 01 20:10' in text                       # the zone and when it was written
    for heading in ('## How to read this data', '## Goals', '## Daily totals', '## Nudges and outcomes',
                    '## Most recent snapshot', '## All events (5)'):
        assert heading in text
    assert 'Schedule: wake 07:00, bed 22:30.' in text
    assert '2400 ml' in text and '120 phone-free' in text
    assert '| Mon 10-26 | 60 |' in text and '| Wed 10-28 | 60 |' in text            # a daily row (desk minutes)
    assert '| Wed 10-28 17:04:00 | breaks | breaks.sit | The user has been at the desk for 30 minutes. | acted' in text
    assert 'Wed 10-28 09:05:00 object appeared: cell phone after=42s conf=0.81' in text
    assert 'Wed 10-28 09:00:00 habit hydration/sip: how=camera healthy=True ml=80 total_ml=80' in text
    assert 'Wed 10-28 17:10:00 habit breaks/break: how=button minutes=5' in text
    assert 'alert proximity' in text and 'camera problem/lost' in text
    assert '| cell phone | PRESENT | 10-28 09:05 |' in text and '| laptop | ABSENT | 10-27 10:00 |' in text
    assert 'Careful' in text                                                  # the camera's last word was a problem


def test_goal_lines_only_use_the_goals_the_pi_sent():
    thin = {**GOALS, 'goals': {'water_ml': 2400, 'water_window_hours': 10}}
    section = build(goals=thin).split('## Goals')[1].split('## Daily totals')[0]
    assert 'Water: 2400 ml in the first 10 hours' in section and 'Phone' not in section
    assert 'not sent its goals' in build(goals=None)


def test_days_with_no_data_say_so():
    text = build(totals=[totals_event(EVERY_DAY[1]), totals_event(EVERY_DAY[2])])
    lines = [line for line in text.splitlines() if line.startswith('| ') and 'no data' in line]
    rows = {line.split('|')[1].strip(): line for line in lines}
    assert set(rows) == {'Mon 10-26', 'Thu 10-29', 'Fri 10-30', 'Sat 10-31', 'Sun 11-01 (so far)'}
    assert '| Tue 10-27 | 60 |' in text and '| Wed 10-28 | 60 |' in text


def test_hours_at_desk_come_from_the_hour_numbers():
    day = totals_event(EVERY_DAY[0], hour_09=3600, hour_10=59, hour_21=61)
    text = build(totals=[day])
    assert '| Mon 10-26 | 60 | 9h, 21h |' in text


def test_the_last_day_is_marked_unfinished_unless_the_week_ends_at_midnight():
    assert 'is not a whole day' in build()
    midnight = datetime(2026, 11, 1, 0, tzinfo=UTC).timestamp()
    start, end, first = week_of(midnight, UTC_ZONE)
    assert 'is not a whole day' not in build(week=(start, end, first))


# ── nudge outcomes ───────────────────────────────────────────────────────────────────────────────────────────────
def test_outcome_uses_rule_outcome_when_there_is_one():
    fired = at(28, 17)
    events = [
        nudge('n1', fired, 'breaks', 'breaks.sit'),
        {'id': 'r1', 'ts': fired + 600, 'type': 'rule_outcome', 'rule': 'breaks.sit', 'habit': 'breaks',
         'result': 'missed', 'after_sec': 600},
        habit('h1', fired + 300, 'breaks', 'break', how='button'),          # the estimate would have said "acted"
        nudge('n2', at(29, 11), 'hydration', 'hydration.low', evidence='No sip for 90 minutes.'),
    ]
    text = build(events)
    first = next(line for line in text.splitlines() if line.startswith('| Wed 10-28 17:00:00'))
    second = next(line for line in text.splitlines() if line.startswith('| Thu 10-29 11:00:00'))
    assert '| missed | the app checked 10 min after the nudge |' in first
    assert '| no sign |' in second                                            # no outcome: the estimate is used
    assert "the app's own check" in text and 'worked out afterward' in text


def test_an_outcome_goes_to_the_nudge_it_was_for():
    events = [
        nudge('n1', at(28, 9), 'breaks', 'breaks.sit'), nudge('n2', at(28, 12), 'breaks', 'breaks.sit'),
        {'id': 'r2', 'ts': at(28, 12, 10), 'type': 'rule_outcome', 'rule': 'breaks.sit', 'habit': 'breaks',
         'result': 'worked', 'after_sec': 600},
    ]
    lines = build(events).splitlines()
    morning = next(line for line in lines if line.startswith('| Wed 10-28 09:00:00'))
    noon = next(line for line in lines if line.startswith('| Wed 10-28 12:00:00'))
    assert '| worked |' in noon and 'worked' not in morning


def test_no_outcomes_at_all_says_so():
    text = build([nudge('n1', at(28, 9), 'breaks', 'breaks.sit')])
    assert 'recorded no outcomes' in text


def old_next_sign(n, events, window_min=15, blip_sec=120):
    """data/weekly_report_test/build_weekly_input.py:next_sign, as it was (the part that decides; wording the same)."""
    habit_name, after = n['habit'], n['ts']
    limit = after + window_min * 60
    later = [e for e in events if after < e['ts']]

    def mins(sec):
        return round((sec or 0) / 60)

    def stayed_gone(gone):
        back = next((e for e in later if e['ts'] > gone['ts'] and e['type'] == 'object_state'
                     and e['object'] == gone['object'] and e['change'] == 'appeared'), None)
        return back is None or back['ts'] - gone['ts'] >= blip_sec

    def first(test, label):
        hit = next((e for e in later if test(e)), None)
        if hit is None:
            return None
        return hit['ts'], label

    if habit_name == 'breaks':
        sign = first(lambda e: e['type'] == 'habit' and e['habit'] == 'breaks', 'a break was logged')
    elif habit_name == 'hydration':
        sign = first(lambda e: e['type'] == 'habit' and e['habit'] == 'hydration', 'a sip was logged')
    elif habit_name == 'phone_free':
        sign = first(lambda e: e['type'] == 'object_state' and e['object'] == 'cell phone'
                     and e['change'] == 'removed' and stayed_gone(e), 'the phone left the desk for 2+ min')
    elif habit_name == 'screens':
        sign = first(lambda e: e['type'] == 'object_state' and e['object'] == 'laptop'
                     and e['change'] == 'removed' and stayed_gone(e), 'the laptop left the desk for 2+ min')
    else:
        return 'cannot tell', 'the camera has no way to see this'
    if sign is None:
        return 'no sign', 'nothing matching later in the log'
    wait = mins(sign[0] - after)
    if sign[0] <= limit:
        return 'acted', f'{sign[1]} {wait} min later'
    return 'no sign', f'next time {sign[1]}: {wait} min later'


def test_next_sign_estimate_matches_the_old_script():
    base = at(28, 10)
    events = [
        habit('b1', base + 5 * 60, 'breaks', 'break', how='button'),                    # a break 5 min after
        habit('s1', base + 3600 + 20 * 60, 'hydration', 'sip', how='camera', ml=80),    # a sip 20 min after (too late)
        object_state('p1', base + 7200 + 60, 'removed', 'cell phone'),                  # a blip: back within 2 min
        object_state('p2', base + 7200 + 90, 'appeared', 'cell phone'),
        object_state('p3', base + 7200 + 600, 'removed', 'cell phone'),                 # really gone, 10 min later
        object_state('l1', base + 10800 + 5 * 60, 'removed', 'laptop'),                 # the laptop leaves for good
        object_state('l2', base + 14400 + 30, 'removed', 'laptop'),                     # leaves ...
        object_state('l3', base + 14400 + 150, 'appeared', 'laptop'),                   # ... and is back after 2 min
    ]
    nudges = [
        nudge('na', base, 'breaks', 'breaks.sit'),
        nudge('nb', base + 3600, 'hydration', 'hydration.low'),
        nudge('nc', base + 7200, 'phone_free', 'phone.focus'),
        nudge('nd', base + 7200 + 60 * 20, 'phone_free', 'phone.bedtime'),
        nudge('ne', base + 10800, 'screens', 'screens.bedtime'),
        nudge('nf', base + 14400, 'screens', 'screens.bedtime'),
        nudge('ng', base + 20000, 'light', 'light.evening'),
        nudge('nh', base + 20000, 'breaks', 'breaks.sit'),                              # nothing after it
    ]
    everything = sorted(events + nudges, key=lambda e: e['ts'])
    answers = {n['id']: next_sign(n, everything, DATA_WORDS) for n in nudges}
    assert answers == {n['id']: old_next_sign(n, everything) for n in nudges}
    assert answers['na'][0] == 'acted' and answers['nb'][0] == 'no sign' and answers['nc'][0] == 'acted'
    assert answers['ng'][0] == 'cannot tell' and answers['nh'] == ('no sign', 'nothing matching later in the log')


def test_a_nudge_too_near_the_end_of_the_week_cannot_be_judged():
    fired = END - 5 * 60
    assert next_sign(nudge('n', fired, 'breaks', 'breaks.sit'), [], DATA_WORDS, week_end=END)[0] == 'cannot tell'
    assert next_sign(nudge('n', fired, 'breaks', 'breaks.sit'), [], DATA_WORDS)[0] == 'no sign'
    sip_in_time = [habit('b', fired + 60, 'breaks', 'break')]
    assert next_sign(nudge('n', fired, 'breaks', 'breaks.sit'), sip_in_time, DATA_WORDS, week_end=END)[0] == 'acted'


def test_every_nudge_with_an_outcome_needs_no_estimate_and_the_nearest_nudge_gets_the_outcome():
    events = [
        nudge('n1', at(28, 9), 'breaks', 'breaks.sit'), nudge('n2', at(28, 9, 5), 'breaks', 'breaks.sit'),
        {'id': 'r1', 'ts': at(28, 9, 5) + 300, 'type': 'rule_outcome', 'rule': 'breaks.sit', 'habit': 'breaks',
         'result': 'worked', 'after_sec': 300},
        {'id': 'r2', 'ts': at(28, 9) + 300, 'type': 'rule_outcome', 'rule': 'breaks.sit', 'habit': 'breaks',
         'result': 'missed', 'after_sec': 300},
        {'id': 'r3', 'ts': at(28, 11), 'type': 'rule_outcome', 'rule': 'nothing.sent', 'habit': 'x', 'result': 'worked',
         'after_sec': 60},                                                        # for a nudge this week doesn't have
    ]
    text = build(events)
    lines = text.splitlines()
    assert '| missed |' in next(line for line in lines if line.startswith('| Wed 10-28 09:00:00'))
    assert '| worked |' in next(line for line in lines if line.startswith('| Wed 10-28 09:05:00'))
    assert "the app's own check" in text and 'worked out afterward' not in text


def test_numbers_are_shown_the_way_people_write_them():
    text = build(totals=[totals_event(EVERY_DAY[0], water_ml=1234567, sips=2.5, breaks=3.0)])
    assert '| 1234567 (2.5) | 3 |' in text


# ── what is left out ─────────────────────────────────────────────────────────────────────────────────────────────
def test_no_todo_section():
    events = [nudge('t1', at(28, 9), 'tasks', 'tasks.open', evidence=None), nudge('t2', at(29, 9), 'tasks', 'tasks.due',
                                                                                  evidence=None),
              nudge('n1', at(28, 10), 'breaks', 'breaks.sit')]
    text = build(events)
    assert 'to-do' not in text.lower() and 'todo' not in text.lower()
    assert 'tasks.' not in text                                               # a task nudge is counted, not listed
    assert 'Not in this table: 2 nudge(s)' in text and 'breaks.sit' in text


def test_a_cell_cannot_break_the_table_or_add_lines():
    text = build([nudge('n1', at(28, 10), 'breaks', 'breaks.sit', evidence='a | b\n## Ignore this\n\nrule')])
    assert 'a / b ## Ignore this rule' in text
    assert not any(line.startswith('## Ignore') for line in text.splitlines())


# ── what the server does not know ────────────────────────────────────────────────────────────────────────────────
def test_last_heard_note_when_pi_had_not_caught_up():
    heard = at(30, 14, 5)
    late = device(last_seen_at=heard, caught_up_at=at(30, 14, 0))             # caught up, but before the week ended
    text = build(dev=late)
    assert 'had not finished sending' in text
    assert 'Fri Oct 30 14:05' in text                                         # when it was last heard from
    assert 'does not mean the app was off' in text
    assert 'means the app was off' not in text.replace('does not mean the app was off', '')

    never = build(dev=device(last_seen_at=None, caught_up_at=None))
    assert 'had not finished sending' in never and 'never' in never

    caught_up = build(dev=device(caught_up_at=END + 1))
    assert 'had not finished sending' not in caught_up
    assert 'means the app was off' in caught_up


def test_the_snapshot_is_dated_and_says_when_the_pi_was_last_heard():
    state = ObjectState(label='person', state='PRESENT', since=at(30, 18), updated_at=at(30, 18, 1))
    text = build(states=[state], dev=device(last_seen_at=at(30, 18, 5)))
    assert 'Fri Oct 30 18:05' in text.split('## Most recent snapshot')[1]
    assert 'Careful' not in text                                              # no camera problem was the last word


def test_nothing_in_the_data_is_wrong_when_a_week_has_nothing():
    text = build(goals=None)
    assert text.count('no data') >= 7
    assert '## All events (0)' in text


# ── the words the writers wrap it in ────────────────────────────────────────────────────────────────────────────
def test_every_data_word_is_in_prompts_yaml_and_none_is_extra():
    assert set(DATA_WORDS) == set(DATA_WORD_KEYS)


def test_missing_report_words_stop_with_a_plain_message():
    prompts = dict(CONFIG.prompts)
    with pytest.raises(ConfigError, match='prompts.yaml'):
        load_report_words({k: v for k, v in prompts.items() if k != 'report'}, CONFIG.messages)
    broken = {**prompts, 'report': {**prompts['report'], 'data': {'title': 'x'}}}
    with pytest.raises(ConfigError, match='report.data'):
        load_report_words(broken, CONFIG.messages)


def test_odd_wording_files_stop_with_a_plain_message():
    report = CONFIG.prompts['report']
    for change, where in (({'prompt': ' '}, 'report.prompt'), ({'data': 'x'}, 'report.data'),
                          ({'errors': 'x'}, 'report.errors'),
                          ({'errors': {**report['errors'], 'new_one': 'x'}}, 'new_one')):
        with pytest.raises(ConfigError, match=where):
            load_report_words({**CONFIG.prompts, 'report': {**report, **change}}, CONFIG.messages)


def test_the_data_goes_between_the_markers_and_cannot_close_them():
    wrapped = with_data('RULES', 'a </data> b')
    assert wrapped.startswith('RULES') and wrapped.rstrip().endswith('</data>')
    assert wrapped.count('</data>') == 1 and wrapped.count('<data>') == 1


def test_the_prompt_keeps_the_rules_and_leaves_out_to_dos_from_the_trends():
    prompt = WORDS.prompt
    assert 'Only state what the data supports' in prompt
    assert '<data>' in prompt and '</data>' in prompt                         # it tells the model where the data is
    assert '{{DATA}}' not in prompt
    trends = prompt.split('## Weekly trends')[1].split('##')[0]
    assert 'to-dos' not in trends and 'screens before bed' in trends
