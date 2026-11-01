"""
The data a weekly report is written from, and the words around it.

build_week() turns one Pi's stored events into the markdown the AI reads: the old test script
(data/weekly_report_test/build_weekly_input.py) working from what the server keeps. Nothing about to-dos or the
wording of messages is in it, because neither is ever sent to the server. Every sentence in it comes from
cloud/prompts.yaml (report.data); this code only fills in the blanks. It never says anything the server doesn't
know: a day with no numbers is called "no data" only when the Pi had sent everything, and when a Pi had not finished
sending (or was linked part way through the week) the notes say so instead of saying the app was off.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from desktalk_cloud.config import ConfigError, Messages, ReportSettings
from desktalk_cloud.domain import Device, ObjectState
from desktalk_cloud.report_times import DAYS_IN_WEEK, zone_name, zone_of

__all__ = ['DATA_WORD_KEYS', 'ERROR_WORD_KEYS', 'ReportWords', 'build_week', 'load_report_words', 'next_sign',
           'with_data']

# Every wording report.data in cloud/prompts.yaml must have (and no other).
DATA_WORD_KEYS = (
    'title', 'times', 'last_day_open', 'h_read', 'read_notes', 'no_data_means_off', 'not_caught_up', 'never_heard',
    'linked_note', 'h_goals', 'schedule', 'goal_lines', 'goals_missing', 'h_daily', 'daily_note', 'daily_header',
    'first_evening_note', 'total_label', 'total_part_known', 'no_data_day', 'numbers_missing', 'before_linked',
    'first_evening', 'week_starts', 'last_row_open', 'last_row_as_of', 'last_row_after_end', 'hour_label', 'hours_none',
    'unknown_number', 'breaks_split', 'breaks_part', 'breaks_other', 'h_gaps', 'gaps_intro', 'gaps_none',
    'gaps_after_heard', 'gaps_never_heard', 'gap_line', 'duration_hm', 'duration_m', 'h_nudges', 'outcomes_none',
    'outcomes_app', 'outcomes_estimate', 'nudge_header', 'no_nudges', 'tasks_left_out', 'detail_app', 'result_acted',
    'result_no_sign', 'result_cannot_tell', 'sign_break', 'sign_sip', 'sign_phone', 'sign_laptop', 'detail_cannot_tell',
    'detail_nothing_later', 'detail_acted', 'detail_late', 'detail_week_ended', 'h_snapshot', 'snapshot_intro',
    'snapshot_none', 'camera_problem', 'snapshot_header', 'all_objects_intro', 'h_events', 'events_note')

# The reasons a report failed (report.errors in cloud/prompts.yaml), and `no_key`, which is messages.report_no_key.
ERROR_WORD_KEYS = ('status', 'key_refused', 'unreachable', 'timeout', 'empty', 'cut_off', 'refused', 'bad_reply',
                   'unexpected', 'pi_removed')

DATA_OPEN, DATA_CLOSE = '<data>', '</data>'
MARKER = re.compile(r'<(?=\s*/?\s*data)', re.IGNORECASE)      # the start of anything that looks like a data tag
PROMPTS_FILE = 'cloud/prompts.yaml'
HOURS_IN_DAY = 24
SECONDS_IN_MINUTE, MINUTES_IN_HOUR = 60, 60
TASKS = 'tasks'                       # the habit of a nudge about the person's task list
GOALS = 'goals'
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
    """What the AI is sent: the rules, then the data between <data> and </data>. However the data spells a data tag
    (any case, spaces inside), it can't close or reopen the marker."""
    return f'{prompt}\n\n{DATA_OPEN}\n{MARKER.sub("&lt;", _without_format_characters(data))}\n{DATA_CLOSE}'


# ── small helpers ────────────────────────────────────────────────────────────────────────────────────────────────
def _number(value: Any) -> float | None:
    """The value if it is a number that sums can use, else None. (math.isfinite raises on a whole number too big for
    float maths.)"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _show(value: float | None, unknown: str = '?') -> str:
    """A number as people write it: 3360 not 3360.0 or 3.36e+03."""
    if value is None:
        return unknown
    return str(int(value)) if value == int(value) else f'{value:.1f}'


def _minutes(seconds: float | None) -> int:
    return round((seconds or 0) / SECONDS_IN_MINUTE)


def _minutes_or(seconds: float | None, unknown: str) -> str:
    return unknown if seconds is None else str(_minutes(seconds))


def _without_format_characters(text: str) -> str:
    """The text without invisible format characters (zero-width spaces and joiners, direction marks ...), which could
    hide a data tag or a line break from a check."""
    return ''.join(c for c in text if unicodedata.category(c) != 'Cf')


def _clean(text: Any) -> str:
    """One line of plain text: no line breaks, no invisible characters, no table bars, no backticks that could end a
    code block."""
    return ' '.join(_without_format_characters(str(text)).split()).replace('|', '/').replace('`', "'")


def _is_tasks(event: Mapping[str, Any]) -> bool:
    rule = event.get('rule')
    return event.get('habit') == TASKS or (isinstance(rule, str) and rule.startswith(f'{TASKS}.'))


def _fill(template: Any, **values: Any) -> str:
    return str(template).format(**values)


@dataclass(frozen=True)
class _Ctx:
    """What every part of the data needs: the Pi's clock, the week, the wording and the settings."""

    zone: tzinfo
    start: float
    end: float
    words: Mapping[str, Any]
    settings: ReportSettings

    @property
    def unknown(self) -> str:
        return str(self.words['unknown_number'])

    def when(self, ts: float, pattern: str = '%a %b %d %H:%M') -> str:
        return datetime.fromtimestamp(ts, self.zone).strftime(pattern)

    def stamp(self, ts: float) -> str:
        return self.when(ts, '%a %m-%d %H:%M:%S')

    def day_of(self, ts: float) -> date:
        return datetime.fromtimestamp(ts, self.zone).date()


# ── "what happened after the nudge", worked out from the log ─────────────────────────────────────────────────────
def next_sign(nudge: Mapping[str, Any], events: Sequence[Mapping[str, Any]], words: Mapping[str, Any],
              settings: ReportSettings, week_end: float | None = None) -> tuple[str, str]:
    """What the log shows after a nudge: (acted | no sign | cannot tell, in plain words). It only says what happened
    next, not that the nudge caused it. Same answers as `next_sign` in data/weekly_report_test/build_weekly_input.py.
    With `week_end`, a nudge sent less than the window before the week's data ends can't be judged."""
    window, blip = settings.outcome_window_min, settings.blip_sec
    habit, after = nudge.get('habit'), nudge['ts']
    later = sorted((e for e in events if e['ts'] > after), key=lambda e: e['ts'])
    blip_min = _show(blip / SECONDS_IN_MINUTE)

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
        sign = first(left('cell phone'), _fill(words['sign_phone'], minutes=blip_min))
    elif habit == 'screens':
        sign = first(left('laptop'), _fill(words['sign_laptop'], minutes=blip_min))
    else:
        return str(words['result_cannot_tell']), str(words['detail_cannot_tell'])
    if sign is None:
        if week_end is not None and after + window * SECONDS_IN_MINUTE > week_end:
            return str(words['result_cannot_tell']), _fill(words['detail_week_ended'], window=_show(window))
        return str(words['result_no_sign']), str(words['detail_nothing_later'])
    wait = _minutes(sign[0] - after)
    if sign[0] <= after + window * SECONDS_IN_MINUTE:
        return str(words['result_acted']), _fill(words['detail_acted'], sign=sign[1], minutes=wait)
    return str(words['result_no_sign']), _fill(words['detail_late'], sign=sign[1], minutes=wait)


def _app_outcomes(nudges: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]], tolerance_sec: float
                  ) -> dict[str, Mapping[str, Any]]:
    """The app's own outcome for each nudge that has one, by nudge id. An outcome is for the nudge of the same rule
    that was sent closest to the time the outcome says the nudge was sent (its time minus `after_sec`), and only when
    that nudge is within `tolerance_sec` of it: an outcome for a nudge from before the week belongs to no one here."""
    best: dict[str, tuple[float, Mapping[str, Any]]] = {}
    for outcome in outcomes:
        after_sec = _number(outcome.get('after_sec'))
        same = [n for n in nudges if n.get('rule') == outcome.get('rule') and n.get('habit') == outcome.get('habit')]
        if not same or after_sec is None or not isinstance(outcome.get('result'), str):
            continue
        sent_at = outcome['ts'] - after_sec
        nudge = min(same, key=lambda n: abs(n['ts'] - sent_at))
        gap = abs(nudge['ts'] - sent_at)
        if gap <= tolerance_sec and (nudge['id'] not in best or gap < best[nudge['id']][0]):
            best[nudge['id']] = (gap, outcome)
    return {nudge_id: outcome for nudge_id, (_, outcome) in best.items()}


# ── the parts of the data ────────────────────────────────────────────────────────────────────────────────────────
def _event_line(event: Mapping[str, Any], when: str) -> str:
    kind = event.get('type')
    if kind == 'object_state':
        confidence, dwell = _number(event.get('conf_avg')), _number(event.get('dwell_sec'))
        stay = f' after={round(dwell)}s' if dwell is not None else ''
        conf = f' conf={confidence:.2f}' if confidence is not None else ''
        return f"{when} object {_clean(event.get('change', '?'))}: {_clean(event.get('object', '?'))}{stay}{conf}"
    if kind == 'habit':
        bits = [f'{key}={_clean(event[key])}' for key in ('how', 'healthy') if key in event]
        bits += [f'{_clean(key)}={_show(number)}' for key, value in event.items()
                 if key not in PLAIN_HABIT_FIELDS and (number := _number(value)) is not None]
        return f"{when} habit {_clean(event.get('habit', '?'))}/{_clean(event.get('action', '?'))}:" + \
            (' ' + ' '.join(bits) if bits else '')
    if kind == 'alert':
        extra = ''.join(f' {key}={_clean(event[key])}' for key in ('scenario', 'feature') if event.get(key))
        return f"{when} alert {_clean(event.get('kind', '?'))}{extra}"
    return f"{when} camera {_clean(event.get('kind', '?'))}/{_clean(event.get('key', '?'))}"


def _coverage_notes(ctx: _Ctx, device: Device, heard: str) -> list[str]:
    """What the server can and can't say about the days with nothing: whether the Pi had finished sending, and
    whether the Pi was even linked yet."""
    words = ctx.words
    caught_up = device.caught_up_at is not None and device.caught_up_at >= ctx.end
    notes = [str(words['no_data_means_off']) if caught_up else _fill(words['not_caught_up'], last_heard=heard)]
    if device.created_at > ctx.start:
        notes.append(_fill(words['linked_note'], linked=ctx.when(device.created_at)))
    return notes


def _goal_lines(ctx: _Ctx, goals: Mapping[str, Any] | None) -> list[str]:
    words = ctx.words
    out = [str(words['h_goals']) + '\n']
    numbers: dict[str, Any] = {}
    if goals is not None and isinstance(goals.get('goals'), Mapping):
        numbers = {name: v for name, v in goals['goals'].items() if _number(v) is not None}     # (no number: skipped)
    lines: list[str] = []
    for template in words['goal_lines']:
        try:
            lines.append(str(template).format_map(numbers))
        except (KeyError, ValueError, IndexError, TypeError):
            continue                                         # a number this line needs was not sent
    wake, bed = (goals.get('wake'), goals.get('bed')) if goals else (None, None)
    if wake or bed:
        out.append(_fill(words['schedule'], wake=_clean(wake or '?'), bed=_clean(bed or '?')) + '\n')
    out.extend(lines or [str(words['goals_missing'])])
    out.append('')
    return out


def _breaks_cell(ctx: _Ctx, total: float | None, how: Counter[str], remark: bool) -> str:
    """The day's break count, split by how each was logged (a button, leaving the desk, the camera being off)."""
    words = ctx.words
    if not how:
        return _show(total, ctx.unknown)
    parts = [_fill(words['breaks_part'], count=count, how=_clean(name))
             for name, count in sorted(how.items(), key=lambda item: (-item[1], item[0]))]
    if remark and total is not None and total > sum(how.values()):
        parts.append(_fill(words['breaks_other'], count=_show(total - sum(how.values()))))
    return _fill(words['breaks_split'], total=_show(total, ctx.unknown), parts=', '.join(parts))


def _daily_rows(ctx: _Ctx, device: Device, totals: Sequence[Mapping[str, Any]], ordered: Sequence[Mapping[str, Any]],
                days: Sequence[date], first_evening: date | None, last_open: date | None) -> list[str]:
    words, unknown = ctx.words, ctx.unknown
    columns = str(words['daily_header']).count('|') - 1
    by_day = {t.get('day'): t for t in totals if isinstance(t.get('totals'), Mapping)}
    event_days = {ctx.day_of(e['ts']) for e in ordered if e.get('type') != GOALS}
    how_by_day: dict[date, Counter[str]] = {}
    for e in ordered:
        if e.get('type') == 'habit' and e.get('habit') == 'breaks' and e.get('action') == 'break':
            how_by_day.setdefault(ctx.day_of(e['ts']), Counter())[str(e.get('how', '?'))] += 1
    linked_day = ctx.day_of(device.created_at)
    rows: list[str] = []
    counted: list[dict[str, float | None]] = []              # every row but the first evening's, to add up
    for day in days:
        label = f'{day:%a %m-%d}'
        stored = by_day.get(day.isoformat())
        found = stored['totals'] if stored is not None else None
        if day == first_evening:
            label += _fill(words['first_evening' if found else 'week_starts'], time=ctx.when(ctx.start, '%H:%M'))
        elif day == last_open:
            if stored is None:
                label += _fill(words['last_row_open'], time=ctx.when(ctx.end, '%H:%M'))
            elif stored['ts'] > ctx.end:
                day_over = datetime.combine(day + timedelta(days=1), time(0), tzinfo=ctx.zone).timestamp()
                label += _fill(words['last_row_after_end'], sent=ctx.when(stored['ts']), end=ctx.when(ctx.end),
                               until=ctx.when(min(stored['ts'], day_over)))
            else:
                label += _fill(words['last_row_as_of'], sent=ctx.when(stored['ts']))
        if found is None:
            note = (words['before_linked'] if day < linked_day else
                    words['numbers_missing'] if day in event_days else words['no_data_day'])
            rows.append('| ' + ' | '.join([label, str(note)] + [''] * (columns - 2)) + ' |')
            continue
        value = {name: _number(found.get(name)) for name in DAILY_FIELDS}
        desk_hours = [_fill(words['hour_label'], hour=h) for h in range(HOURS_IN_DAY)
                      if (_number(found.get(f'hour_{h:02d}')) or 0) >= ctx.settings.hour_min_sec]
        if day != first_evening:
            counted.append(value)
        breaks = _breaks_cell(ctx, value['breaks'], how_by_day.get(day, Counter()), remark=day != first_evening)
        cells = [label, _minutes_or(value['desk_sec'], unknown), ', '.join(desk_hours) or str(words['hours_none']),
                 f"{_show(value['water_ml'], unknown)} ({_show(value['sips'], unknown)})", breaks,
                 _minutes_or(value['longest_sit_sec'], unknown), _minutes_or(value['phone_free_sec'], unknown),
                 _minutes_or(value['phone_on_desk_sec'], unknown), _show(value['sunlight_minutes'], unknown),
                 _show(value['nudges_sent'], unknown)]
        rows.append('| ' + ' | '.join(cells) + ' |')
    if counted:
        rows.append(_total_row(ctx, counted))
    return rows


def _added_up(numbers: Sequence[float]) -> float | None:
    """The sum, or None when it is too big for sums to use."""
    try:
        return _number(sum(numbers))
    except OverflowError:
        return None


def _total_row(ctx: _Ctx, counted: Sequence[Mapping[str, float | None]]) -> str:
    """This week's rows added up (the longest sit is the longest of them), so nobody has to add them in their head
    or add the first evening's row, which was in last week's report. A week's last day always counts, even when its
    numbers were sent after the week ended: the store keeps only a day's newest numbers, and next week's report leaves
    the day out as its first evening. A column no row knows shows the unknown mark, not 0, and one only some rows know
    says the total covers just those."""
    words, unknown = ctx.words, ctx.unknown

    def column(name: str, show: Callable[[float], str], combine: Callable[[Sequence[float]], float | None] = _added_up
               ) -> str:
        known = [number for value in counted if (number := value[name]) is not None]
        total = combine(known) if known else None
        if total is None:
            return unknown
        if len(known) == len(counted):
            return show(total)
        return _fill(words['total_part_known'], total=show(total), known=len(known), count=len(counted))

    def minutes(seconds: float) -> str:
        return str(_minutes(seconds))

    cells = [_fill(words['total_label'], count=len(counted)), column('desk_sec', minutes), '',
             f"{column('water_ml', _show)} ({column('sips', _show)})", column('breaks', _show),
             column('longest_sit_sec', minutes, max), column('phone_free_sec', minutes),
             column('phone_on_desk_sec', minutes), column('sunlight_minutes', _show), column('nudges_sent', _show)]
    return '| ' + ' | '.join(cells) + ' |'


def _length(ctx: _Ctx, seconds: float) -> str:
    hours, minutes = divmod(_minutes(seconds), MINUTES_IN_HOUR)
    return _fill(ctx.words['duration_hm'], hours=hours, minutes=minutes) if hours else \
        _fill(ctx.words['duration_m'], minutes=minutes)


def _gap_lines(ctx: _Ctx, device: Device, ordered: Sequence[Mapping[str, Any]], heard: str) -> list[str]:
    """Stretches of the week with no event of any kind, from the first moment the server could have heard from this
    Pi to the last. Silence can mean the app was off or that nothing on the desk changed, and the data can't tell
    which. A Pi that had not finished sending is listed only up to when it was last heard from: after that, what
    happened has not arrived."""
    words, limit = ctx.words, ctx.settings.gap_min * SECONDS_IN_MINUTE
    caught_up = device.caught_up_at is not None and device.caught_up_at >= ctx.end
    first = max(ctx.start, device.created_at)
    last = ctx.end if caught_up else min(ctx.end, device.last_seen_at if device.last_seen_at is not None else first)
    gap_min = _show(ctx.settings.gap_min)
    out = [str(words['h_gaps']) + '\n']
    if device.last_seen_at is None and not caught_up:
        return [*out, str(words['gaps_never_heard']), '']
    marks = [first, *(e['ts'] for e in ordered if first <= e['ts'] < last), last]
    gaps = [(a, b) for a, b in zip(marks, marks[1:], strict=False) if b - a >= limit]
    if gaps:
        out.append(_fill(words['gaps_intro'], gap=gap_min) + '\n')
        out.extend(_fill(words['gap_line'], start=ctx.when(a, '%a %m-%d %H:%M'), end=ctx.when(b, '%a %m-%d %H:%M'),
                         length=_length(ctx, b - a)) for a, b in gaps)
    else:
        out.append(_fill(words['gaps_none'], gap=gap_min))
    if not caught_up:
        out.append('\n' + _fill(words['gaps_after_heard'], last_heard=heard))
    out.append('')
    return out


def _nudge_lines(ctx: _Ctx, ordered: Sequence[Mapping[str, Any]]) -> list[str]:
    words = ctx.words
    out = [str(words['h_nudges']) + '\n']
    all_nudges = [e for e in ordered if e.get('type') == 'nudge']
    nudges = [n for n in all_nudges if not _is_tasks(n)]
    outcomes = [e for e in ordered if e.get('type') == 'rule_outcome' and not _is_tasks(e)]
    given = _app_outcomes(nudges, outcomes, ctx.settings.outcome_match_sec)
    window = _show(ctx.settings.outcome_window_min)
    if not outcomes:
        out.append(_fill(words['outcomes_none'], window=window) + '\n')
    else:
        out.append(str(words['outcomes_app']))
        if len(given) < len(nudges):
            out.append(_fill(words['outcomes_estimate'], window=window))
        out.append('')
    if nudges:
        out.append(str(words['nudge_header']))
        out.append('|' + '---|' * (str(words['nudge_header']).count('|') - 1))
        for n in nudges:
            outcome = given.get(n['id'])
            if outcome is not None:
                result = str(outcome['result'])
                detail = _fill(words['detail_app'], minutes=_minutes(_number(outcome['after_sec'])))
            else:
                result, detail = next_sign(n, ordered, words, ctx.settings, ctx.end)
            cells = [ctx.stamp(n['ts']), n.get('habit', ''), n.get('rule', ''), n.get('evidence', ''), result, detail]
            out.append('| ' + ' | '.join(_clean(cell) for cell in cells) + ' |')
    else:
        out.append(str(words['no_nudges']))
    if len(all_nudges) > len(nudges):
        out.append('\n' + _fill(words['tasks_left_out'], count=len(all_nudges) - len(nudges)))
    out.append('')
    return out


def _snapshot_lines(ctx: _Ctx, states: Sequence[ObjectState], last_camera: Mapping[str, Any] | None, heard: str
                    ) -> list[str]:
    words = ctx.words
    out = [str(words['h_snapshot']) + '\n', _fill(words['snapshot_intro'], last_heard=heard) + '\n']
    latest_change = max((s.since for s in states), default=None)
    camera_ts = _number(last_camera.get('ts')) if last_camera is not None else None
    if (last_camera is not None and last_camera.get('kind') == 'problem' and camera_ts is not None
            and (latest_change is None or camera_ts >= latest_change)):
        changed = ctx.stamp(latest_change) if latest_change is not None else str(words['never_heard'])
        out.append(_fill(words['camera_problem'], key=_clean(last_camera.get('key', '?')), at=ctx.stamp(camera_ts),
                         changed=changed) + '\n')
    header = str(words['snapshot_header'])
    separator = '|' + '---|' * (header.count('|') - 1)

    def row(s: ObjectState) -> str:
        return f'| {_clean(s.label)} | {_clean(s.state)} | {ctx.when(s.since, "%m-%d %H:%M")} |'

    present = [s for s in states if s.state == 'PRESENT']
    out.extend([header, separator, *map(row, present)] if present else [str(words['snapshot_none'])])
    if states:
        out.extend(['\n' + str(words['all_objects_intro']) + '\n', header, separator,
                    *map(row, sorted(states, key=lambda s: s.since, reverse=True))])
    out.append('')
    return out


def build_week(device: Device, events: Sequence[Mapping[str, Any]], totals: Sequence[Mapping[str, Any]],
               goals: Mapping[str, Any] | None, states: Sequence[ObjectState], last_camera: Mapping[str, Any] | None,
               now: float, week: tuple[float, float, str], words: Mapping[str, Any], settings: ReportSettings) -> str:
    """One Pi's week as markdown, for the AI to read.

    events: that week's stored events (a nudge's own text, a to-do or a message never reach the server).
    totals: the stored daily_totals events from the day the week starts on through its last day.
    goals: the Pi's latest goals event, or None. states: each object's newest state. last_camera: the newest camera
    notice, or None. week: (start, end, first_day) from report_times.week_of(). now: when this is put together."""
    zone = zone_of(device.time_zone)
    ctx = _Ctx(zone, week[0], week[1], words, settings)
    first_day = date.fromisoformat(week[2])
    days = [first_day + timedelta(days=i) for i in range(DAYS_IN_WEEK)]
    start_local, end_local = datetime.fromtimestamp(ctx.start, zone), datetime.fromtimestamp(ctx.end, zone)
    first_evening = start_local.date() if start_local.time() != time(0) else None
    last_open = end_local.date() if end_local.time() != time(0) else None
    heard = ctx.when(device.last_seen_at) if device.last_seen_at is not None else str(words['never_heard'])
    ordered = sorted(events, key=lambda e: (e['ts'], str(e.get('id', ''))))

    w: list[str] = [_fill(words['title'], first=f'{days[0]:%a %b %d}', last=f'{days[-1]:%a %b %d, %Y}') + '\n']
    times = _fill(words['times'], zone=_clean(zone_name(zone)), start=ctx.when(ctx.start), end=ctx.when(ctx.end),
                  written=ctx.when(now))
    if last_open is not None:
        times += ' ' + _fill(words['last_day_open'], day=f'{last_open:%a %b %d}', end_time=f'{end_local:%H:%M}')
    w += [times + '\n', str(words['h_read']) + '\n', str(words['read_notes']).rstrip('\n'),
          *_coverage_notes(ctx, device, heard), '']
    w += _goal_lines(ctx, goals)
    w += [str(words['h_daily']) + '\n', _fill(words['daily_note'], seconds=_show(settings.hour_min_sec)) + '\n']
    header = str(words['daily_header'])
    w += [header, '|' + '---|' * (header.count('|') - 1),
          *_daily_rows(ctx, device, totals, ordered, ([first_evening] if first_evening else []) + days, first_evening,
                       last_open), '']
    if first_evening is not None and any(t.get('day') == first_evening.isoformat() for t in totals):
        w += [str(words['first_evening_note']), '']
    w += _gap_lines(ctx, device, ordered, heard)
    w += _nudge_lines(ctx, ordered)
    w += _snapshot_lines(ctx, states, last_camera, heard)
    shown = [e for e in ordered if e.get('type') in SHOWN_EVENT_TYPES]
    w += [_fill(words['h_events'], count=len(shown)) + '\n', str(words['events_note']) + '\n', '```',
          *(_event_line(e, ctx.stamp(e['ts'])) for e in shown), '```']
    return '\n'.join(w) + '\n'
