"""
The data a weekly report is written from, and the words around it.

build_week() turns one Pi's stored events into the markdown the AI reads: the old test script
(data/weekly_report_test/build_weekly_input.py) working from what the server keeps. Nothing about to-dos or the
wording of messages is in it, because neither is ever sent to the server. Every sentence in it comes from
cloud/prompts.yaml (report.data); this code only fills in the blanks. It never says anything the server doesn't
know: when a Pi had not finished sending, it says when it was last heard from instead of saying the app was off.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from desktalk_cloud.config import ConfigError, Messages
from desktalk_cloud.domain import Device, ObjectState
from desktalk_cloud.report_times import DAYS_IN_WEEK, zone_of

__all__ = ['DATA_WORD_KEYS', 'ERROR_WORD_KEYS', 'ReportWords', 'build_week', 'load_report_words', 'next_sign',
           'with_data']

# Every wording report.data in cloud/prompts.yaml must have (and no other).
DATA_WORD_KEYS = (
    'outcome_window_min', 'blip_sec', 'hour_min_sec', 'title', 'times', 'last_day_open', 'h_read', 'read_notes',
    'no_data_means_off', 'not_caught_up', 'never_heard', 'h_goals', 'schedule', 'goal_lines', 'goals_missing',
    'h_daily', 'daily_note', 'daily_header', 'no_data_day', 'so_far', 'hour_label', 'hours_none', 'unknown_number',
    'h_nudges', 'outcomes_none', 'outcomes_app', 'outcomes_estimate', 'nudge_header', 'no_nudges', 'tasks_left_out',
    'detail_app', 'result_acted', 'result_no_sign', 'result_cannot_tell', 'sign_break', 'sign_sip', 'sign_phone',
    'sign_laptop', 'detail_cannot_tell', 'detail_nothing_later', 'detail_acted', 'detail_late', 'detail_week_ended',
    'h_snapshot', 'snapshot_intro', 'snapshot_none', 'camera_problem', 'snapshot_header', 'all_objects_intro',
    'h_events', 'events_note')

# The reasons a report failed (report.errors in cloud/prompts.yaml), and `no_key`, which is messages.report_no_key.
ERROR_WORD_KEYS = ('status', 'key_refused', 'unreachable', 'timeout', 'empty', 'cut_off', 'refused', 'bad_reply',
                   'unexpected')

DATA_OPEN, DATA_CLOSE = '<data>', '</data>'
PROMPTS_FILE = 'cloud/prompts.yaml'
HOURS_IN_DAY = 24
SECONDS_IN_MINUTE = 60
TASKS = 'tasks'                       # the habit of a nudge about the person's task list
SHOWN_EVENT_TYPES = ('object_state', 'habit', 'alert', 'camera')
PLAIN_HABIT_FIELDS = frozenset({'id', 'ts', 'type', 'habit', 'action', 'how', 'healthy'})
DAILY_FIELDS = ('desk_sec', 'water_ml', 'sips', 'breaks', 'longest_sit_sec', 'phone_free_sec', 'phone_on_desk_sec',
                'sunlight_minutes', 'nudges_sent')


# ── the words ────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ReportWords:
    """What cloud/prompts.yaml says about the report: the rules the AI is given, the notes around the data, and the
    plain reasons a report failed (which include `no_key`)."""

    prompt: str
    data: Mapping[str, Any]
    errors: Mapping[str, str]


def _check_keys(found: Mapping[str, Any], wanted: Sequence[str], where: str) -> None:
    missing = [key for key in wanted if key not in found]
    if missing:
        raise ConfigError(f"{PROMPTS_FILE}: {where} is missing '{missing[0]}'")
    unknown = sorted(set(found) - set(wanted))
    if unknown:
        raise ConfigError(f"{PROMPTS_FILE}: '{unknown[0]}' in {where} is not a wording the server knows")


def load_report_words(prompts: Mapping[str, Any], messages: Messages) -> ReportWords:
    """The report's words from the raw prompts.yaml (config.prompts). A ConfigError says what is wrong."""
    section = prompts.get('report')
    if not isinstance(section, Mapping):
        raise ConfigError(f"{PROMPTS_FILE} has no 'report' section")
    prompt, data, errors = section.get('prompt'), section.get('data'), section.get('errors')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ConfigError(f"{PROMPTS_FILE}: report.prompt must be the text the AI is given")
    if not isinstance(data, Mapping):
        raise ConfigError(f'{PROMPTS_FILE}: report.data must list the notes around the data')
    if not isinstance(errors, Mapping):
        raise ConfigError(f'{PROMPTS_FILE}: report.errors must list the reasons a report failed')
    _check_keys(data, DATA_WORD_KEYS, 'report.data')
    _check_keys(errors, ERROR_WORD_KEYS, 'report.errors')
    return ReportWords(prompt=prompt.rstrip(), data=dict(data),
                       errors={**errors, 'no_key': messages.report_no_key})


def with_data(prompt: str, data: str) -> str:
    """What the AI is sent: the rules, then the data between <data> and </data>. The data can't close the marker."""
    safe = data.replace(DATA_CLOSE, '</ data>').replace(DATA_OPEN, '< data>')
    return f'{prompt}\n\n{DATA_OPEN}\n{safe}\n{DATA_CLOSE}'


# ── small helpers ────────────────────────────────────────────────────────────────────────────────────────────────
def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _show(value: float | None, unknown: str = '?') -> str:
    """A number as people write it: 3360 not 3360.0 or 3.36e+03."""
    if value is None:
        return unknown
    return str(int(value)) if value == int(value) else f'{value:.1f}'


def _minutes(seconds: float | None) -> int:
    return round((seconds or 0) / SECONDS_IN_MINUTE)


def _minutes_or(seconds: float | None, unknown: str) -> str:
    return unknown if seconds is None else str(_minutes(seconds))


def _clean(text: Any) -> str:
    """One line of plain text: no line breaks, no table bars, no backticks that could end a code block."""
    return ' '.join(str(text).split()).replace('|', '/').replace('`', "'")


def _is_tasks(event: Mapping[str, Any]) -> bool:
    rule = event.get('rule')
    return event.get('habit') == TASKS or (isinstance(rule, str) and rule.startswith(f'{TASKS}.'))


def _fill(template: str, **values: Any) -> str:
    return template.format(**values)


# ── "what happened after the nudge", worked out from the log ─────────────────────────────────────────────────────
def next_sign(nudge: Mapping[str, Any], events: Sequence[Mapping[str, Any]], words: Mapping[str, Any],
              week_end: float | None = None) -> tuple[str, str]:
    """What the log shows after a nudge: (acted | no sign | cannot tell, in plain words). It only says what happened
    next, not that the nudge caused it. Same answers as `next_sign` in data/weekly_report_test/build_weekly_input.py.
    With `week_end`, a nudge sent less than the window before the week's data ends can't be judged."""
    window = float(words['outcome_window_min'])
    blip = float(words['blip_sec'])
    habit, after = nudge.get('habit'), nudge['ts']
    later = sorted((e for e in events if e['ts'] > after), key=lambda e: e['ts'])

    def stayed_gone(gone: Mapping[str, Any]) -> bool:
        """A thing that comes back within a couple of minutes was a blip from the detector, not the person moving it."""
        back = next((e for e in later if e['ts'] > gone['ts'] and e.get('type') == 'object_state'
                     and e.get('object') == gone.get('object') and e.get('change') == 'appeared'), None)
        return back is None or back['ts'] - gone['ts'] >= blip

    def first(test, label: str) -> tuple[float, str] | None:
        hit = next((e for e in later if test(e)), None)
        return None if hit is None else (hit['ts'], label)

    def logged(name: str):
        return lambda e: e.get('type') == 'habit' and e.get('habit') == name

    def left(thing: str):
        return lambda e: (e.get('type') == 'object_state' and e.get('object') == thing and e.get('change') == 'removed'
                          and stayed_gone(e))

    if habit == 'breaks':
        sign = first(logged('breaks'), str(words['sign_break']))
    elif habit == 'hydration':
        sign = first(logged('hydration'), str(words['sign_sip']))
    elif habit == 'phone_free':
        sign = first(left('cell phone'), str(words['sign_phone']))
    elif habit == 'screens':
        sign = first(left('laptop'), str(words['sign_laptop']))
    else:
        return str(words['result_cannot_tell']), str(words['detail_cannot_tell'])
    if sign is None:
        if week_end is not None and after + window * SECONDS_IN_MINUTE > week_end:
            return str(words['result_cannot_tell']), _fill(str(words['detail_week_ended']), window=_show(window))
        return str(words['result_no_sign']), str(words['detail_nothing_later'])
    wait = _minutes(sign[0] - after)
    if sign[0] <= after + window * SECONDS_IN_MINUTE:
        return str(words['result_acted']), _fill(str(words['detail_acted']), sign=sign[1], minutes=wait)
    return str(words['result_no_sign']), _fill(str(words['detail_late']), sign=sign[1], minutes=wait)


def _app_outcomes(nudges: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]]
                  ) -> dict[str, Mapping[str, Any]]:
    """The app's own outcome for each nudge that has one, by nudge id. An outcome is for the nudge of the same rule
    that was sent closest to the time the outcome says the nudge was sent (its time minus `after_sec`)."""
    best: dict[str, tuple[float, Mapping[str, Any]]] = {}
    for outcome in outcomes:
        after_sec = _number(outcome.get('after_sec'))
        same = [n for n in nudges if n.get('rule') == outcome.get('rule') and n.get('habit') == outcome.get('habit')]
        if not same or after_sec is None or not isinstance(outcome.get('result'), str):
            continue
        sent_at = outcome['ts'] - after_sec
        nudge = min(same, key=lambda n: abs(n['ts'] - sent_at))
        gap = abs(nudge['ts'] - sent_at)
        if nudge['id'] not in best or gap < best[nudge['id']][0]:
            best[nudge['id']] = (gap, outcome)
    return {nudge_id: outcome for nudge_id, (_, outcome) in best.items()}


# ── the data ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _event_line(event: Mapping[str, Any], when: str) -> str:
    kind = event.get('type')
    if kind == 'object_state':
        confidence, dwell = _number(event.get('conf_avg')), _number(event.get('dwell_sec'))
        stay = f' after={round(dwell)}s' if dwell is not None else ''
        conf = f' conf={confidence:.2f}' if confidence is not None else ''
        return f"{when} object {_clean(event.get('change', '?'))}: {_clean(event.get('object', '?'))}{stay}{conf}"
    if kind == 'habit':
        bits = [f'{key}={_clean(event[key])}' for key in ('how', 'healthy') if key in event]
        bits += [f'{key}={_show(number)}' for key, value in event.items()
                 if key not in PLAIN_HABIT_FIELDS and (number := _number(value)) is not None]
        return f"{when} habit {_clean(event.get('habit', '?'))}/{_clean(event.get('action', '?'))}:" + \
            (' ' + ' '.join(bits) if bits else '')
    if kind == 'alert':
        extra = ''.join(f' {key}={_clean(event[key])}' for key in ('scenario', 'feature') if event.get(key))
        return f"{when} alert {_clean(event.get('kind', '?'))}{extra}"
    return f"{when} camera {_clean(event.get('kind', '?'))}/{_clean(event.get('key', '?'))}"


def build_week(device: Device, events: Sequence[Mapping[str, Any]], totals: Sequence[Mapping[str, Any]],
               goals: Mapping[str, Any] | None, states: Sequence[ObjectState], last_camera: Mapping[str, Any] | None,
               now: float, week: tuple[float, float, str], words: Mapping[str, Any]) -> str:
    """One Pi's week as markdown, for the AI to read.

    events: that week's stored events, oldest first (a nudge's own text, a to-do or a message never reach the server).
    totals: the stored daily_totals events of the week's seven days. goals: the Pi's latest goals event, or None.
    states: each object's newest state. last_camera: the newest camera notice, or None.
    week: (start, end, first_day) from report_times.week_of(). now: when this is put together."""
    zone = zone_of(device.time_zone)
    start, end, first_day = week
    days = [date.fromisoformat(first_day) + timedelta(days=i) for i in range(DAYS_IN_WEEK)]

    def moment(ts: float, pattern: str = '%a %b %d %H:%M') -> str:
        return datetime.fromtimestamp(ts, zone).strftime(pattern)

    def stamp(ts: float) -> str:
        return moment(ts, '%a %m-%d %H:%M:%S')

    unknown = str(words['unknown_number'])
    end_local = datetime.fromtimestamp(end, zone)
    last_day_open = end_local.time() != time(0)
    heard = moment(device.last_seen_at) if device.last_seen_at is not None else str(words['never_heard'])
    caught_up = device.caught_up_at is not None and device.caught_up_at >= end

    w: list[str] = []
    w.append(_fill(str(words['title']), first=f'{days[0]:%a %b %d}', last=f'{days[-1]:%a %b %d, %Y}') + '\n')
    times = _fill(str(words['times']), zone=device.time_zone, start=moment(start), end=moment(end),
                  written=moment(now))
    if last_day_open:
        times += ' ' + _fill(str(words['last_day_open']), day=f'{days[-1]:%a %b %d}', end_time=f'{end_local:%H:%M}')
    w.append(times + '\n')
    w.append(str(words['h_read']) + '\n')
    w.append(str(words['read_notes']).rstrip('\n'))
    w.append(str(words['no_data_means_off']) if caught_up else _fill(str(words['not_caught_up']), last_heard=heard))
    w.append('')

    # Goals
    w.append(str(words['h_goals']) + '\n')
    numbers: Mapping[str, Any] = {}
    if goals is not None and isinstance(goals.get('goals'), Mapping):
        numbers = goals['goals']
    lines: list[str] = []
    for template in words['goal_lines']:
        try:
            lines.append(str(template).format_map(numbers))
        except (KeyError, ValueError, IndexError):
            continue                                         # a number this line needs was not sent
    wake, bed = (goals.get('wake'), goals.get('bed')) if goals else (None, None)
    if wake or bed:
        w.append(_fill(str(words['schedule']), wake=_clean(wake or '?'), bed=_clean(bed or '?')) + '\n')
    if lines:
        w.extend(lines)
    else:
        w.append(str(words['goals_missing']))
    w.append('')

    # Daily totals
    w.append(str(words['h_daily']) + '\n')
    w.append(str(words['daily_note']) + '\n')
    header = str(words['daily_header'])
    columns = header.count('|') - 1
    w.append(header)
    w.append('|' + '---|' * columns)
    by_day = {t.get('day'): t.get('totals') for t in totals if isinstance(t.get('totals'), Mapping)}
    for day in days:
        label = f'{day:%a %m-%d}' + (str(words['so_far']) if last_day_open and day == end_local.date() else '')
        found = by_day.get(day.isoformat())
        if found is None:
            w.append('| ' + ' | '.join([label, str(words['no_data_day'])] + [''] * (columns - 2)) + ' |')
            continue
        value = {name: _number(found.get(name)) for name in DAILY_FIELDS}
        desk_hours = [_fill(str(words['hour_label']), hour=h) for h in range(HOURS_IN_DAY)
                      if (_number(found.get(f'hour_{h:02d}')) or 0) >= float(words['hour_min_sec'])]
        cells = [label, _minutes_or(value['desk_sec'], unknown), ', '.join(desk_hours) or str(words['hours_none']),
                 f"{_show(value['water_ml'], unknown)} ({_show(value['sips'], unknown)})",
                 _show(value['breaks'], unknown), _minutes_or(value['longest_sit_sec'], unknown),
                 _minutes_or(value['phone_free_sec'], unknown), _minutes_or(value['phone_on_desk_sec'], unknown),
                 _show(value['sunlight_minutes'], unknown), _show(value['nudges_sent'], unknown)]
        w.append('| ' + ' | '.join(cells) + ' |')
    w.append('')

    # Nudges and outcomes
    w.append(str(words['h_nudges']) + '\n')
    ordered = sorted(events, key=lambda e: (e['ts'], str(e.get('id', ''))))
    all_nudges = [e for e in ordered if e.get('type') == 'nudge']
    nudges = [n for n in all_nudges if not _is_tasks(n)]
    left_out = len(all_nudges) - len(nudges)
    outcomes = [e for e in ordered if e.get('type') == 'rule_outcome' and not _is_tasks(e)]
    given = _app_outcomes(nudges, outcomes)
    window = _show(float(words['outcome_window_min']), unknown)
    if not outcomes:
        w.append(_fill(str(words['outcomes_none']), window=window) + '\n')
    else:
        w.append(str(words['outcomes_app']))
        if len(given) < len(nudges):
            w.append(_fill(str(words['outcomes_estimate']), window=window))
        w.append('')
    if nudges:
        w.append(str(words['nudge_header']))
        w.append('|' + '---|' * (str(words['nudge_header']).count('|') - 1))
        for n in nudges:
            outcome = given.get(n['id'])
            if outcome is not None:
                result = str(outcome['result'])
                detail = _fill(str(words['detail_app']), minutes=_minutes(_number(outcome['after_sec'])))
            else:
                result, detail = next_sign(n, ordered, words, end)
            cells = [stamp(n['ts']), n.get('habit', ''), n.get('rule', ''), n.get('evidence', ''), result, detail]
            w.append('| ' + ' | '.join(_clean(cell) for cell in cells) + ' |')
    else:
        w.append(str(words['no_nudges']))
    if left_out:
        w.append('\n' + _fill(str(words['tasks_left_out']), count=left_out))
    w.append('')

    # What the Pi last said was on the desk
    w.append(str(words['h_snapshot']) + '\n')
    w.append(_fill(str(words['snapshot_intro']), last_heard=heard) + '\n')
    latest_change = max((s.since for s in states), default=None)
    if last_camera is not None and last_camera.get('kind') == 'problem' and (
            latest_change is None or _number(last_camera.get('ts')) is not None and last_camera['ts'] >= latest_change):
        changed = stamp(latest_change) if latest_change is not None else str(words['never_heard'])
        w.append(_fill(str(words['camera_problem']), key=_clean(last_camera.get('key', '?')),
                       at=stamp(last_camera['ts']), changed=changed) + '\n')
    present = [s for s in states if s.state == 'PRESENT']
    if present:
        w.append(str(words['snapshot_header']))
        w.append('|' + '---|' * (str(words['snapshot_header']).count('|') - 1))
        w.extend(f'| {_clean(s.label)} | {_clean(s.state)} | {moment(s.since, "%m-%d %H:%M")} |' for s in present)
    else:
        w.append(str(words['snapshot_none']))
    if states:
        w.append('\n' + str(words['all_objects_intro']) + '\n')
        w.append(str(words['snapshot_header']))
        w.append('|' + '---|' * (str(words['snapshot_header']).count('|') - 1))
        w.extend(f'| {_clean(s.label)} | {_clean(s.state)} | {moment(s.since, "%m-%d %H:%M")} |'
                 for s in sorted(states, key=lambda s: s.since, reverse=True))
    w.append('')

    # Every event
    shown = [e for e in ordered if e.get('type') in SHOWN_EVENT_TYPES]
    w.append(_fill(str(words['h_events']), count=len(shown)) + '\n')
    w.append(str(words['events_note']) + '\n')
    w.append('```')
    w.extend(_event_line(e, stamp(e['ts'])) for e in shown)
    w.append('```')
    return '\n'.join(w) + '\n'
