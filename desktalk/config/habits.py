"""The habit rules in config/protocols.yaml. What each setting means is in that file's comments."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterator
from datetime import UTC, timedelta, timezone, tzinfo
from functools import cache
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BeforeValidator, Discriminator, Field, Tag, field_validator, model_validator

from desktalk.config.agents import Habit
from desktalk.config.base import (
    ClockTime,
    Count,
    Fraction,
    Latitude,
    Level,
    Longitude,
    PositiveInt,
    PositiveNumber,
    Words,
    ZeroOrMore,
    _got,
    _Said,
    _Says,
    _Section,
)
from desktalk.domain.habits import HABIT_WORDS
from desktalk.util import parse_hhmm


class Schedule(_Section):
    wake_time: ClockTime
    bed_time: ClockTime
    latitude: Latitude | None
    longitude: Longitude | None
    sunset_fallback: ClockTime


class HydrationRule(_Section):
    enabled: bool
    on_waking_ml: ZeroOrMore
    daily_goal_ml: ZeroOrMore
    goal_window_hours: PositiveNumber
    ml_per_sip: PositiveNumber
    sip_gone_min_sec: ZeroOrMore
    sip_gone_max_sec: ZeroOrMore
    sip_lift_share: Fraction          # lifted: its box rose more than this share of the picture's height
    sip_back_share: Fraction          # put back: within this share of the picture of where it rested
    sip_cover_share: Fraction         # this share of the desk vanishing at once = a covered camera
    sip_cover_pictures: PositiveInt   # ...in this many pictures in a row
    remind_after_min: ZeroOrMore
    behind_by_ml: ZeroOrMore
    stop_before_bed_hours: ZeroOrMore
    on_waking_window_min: ZeroOrMore
    on_waking_done_share: Fraction


class BreaksRule(_Section):
    enabled: bool
    sit_limit_min: PositiveNumber
    break_min: PositiveNumber
    repeat_after_min: ZeroOrMore
    walk_after_meal: bool
    meal_min_on_desk_min: ZeroOrMore
    no_phone_on_breaks: bool
    walk_window_after_meal_min: ZeroOrMore
    walk_repeat_after_hours: ZeroOrMore


class MorningMinutes(_Section):
    clear: ZeroOrMore
    partly_cloudy: ZeroOrMore
    overcast: ZeroOrMore


class LightRule(_Section):
    enabled: bool
    morning_window_min: ZeroOrMore
    morning_minutes: MorningMinutes
    afternoon: bool
    afternoon_start_min_before_sunset: ZeroOrMore
    afternoon_end_min_before_sunset: ZeroOrMore
    evening_dim_before_bed_min: ZeroOrMore
    screens_off_before_bed_min: ZeroOrMore
    night_bright_lux: ZeroOrMore
    day_dim_lux: ZeroOrMore
    night_bright_level: Level
    day_dim_level: Level
    day_dim_min: ZeroOrMore
    repeat_after_min: ZeroOrMore
    morning_grace_min: ZeroOrMore
    evening_bright_min: ZeroOrMore
    bed_soon_min: ZeroOrMore
    day_dim_margin_min: ZeroOrMore
    sunset_guess_before_bed_hours: ZeroOrMore
    screens_until_after_bed_hours: ZeroOrMore


class PhoneFreeRule(_Section):
    enabled: bool
    nudge_after_min: ZeroOrMore
    repeat_after_min: ZeroOrMore
    goal_min_per_day: ZeroOrMore
    celebrate_every_min: ZeroOrMore      # 0 = never cheer
    before_bed_min: ZeroOrMore
    before_bed_on_desk_min: ZeroOrMore
    before_bed_until_after_bed_hours: ZeroOrMore


class FoodRule(_Section):
    enabled: bool
    remind_after_min: ZeroOrMore
    repeat_after_hours: ZeroOrMore
    healthy: Words


class TasksRule(_Section):
    enabled: bool
    remind_after_min: ZeroOrMore
    name_shown_chars: PositiveInt
    due_soon_days: Count              # reminders start this many days before the due date ("due tomorrow" at 1)
    late_repeat_hours: PositiveNumber


# ── The habit rules: protocols.yaml → rules ──
_SECONDS_IN = {'s': 1, 'm': 60, 'h': 3600}
_DURATION = re.compile(r'(\d+(?:\.\d+)?)([smh])')
_OFFSET = re.compile(r'([+-])(\d{2}):(\d{2})')
_MINUTES_IN_HOUR = 60


def _seconds(value: Any) -> float:
    """'30s', '5m' or '1.5h' → seconds. A bare number is refused: it doesn't say what the unit is."""
    if isinstance(value, str):
        found = _DURATION.fullmatch(value.strip())
        if found:
            return float(found[1]) * _SECONDS_IN[found[2]]
    raise ValueError('must be a time like 30m')


Duration = Annotated[float, BeforeValidator(_seconds), _Says('a time like 30m')]     # stored as seconds


class _ComputerZone(tzinfo):
    """The clock of this computer, including its daylight saving changes (the standard library has no such zone)."""

    def utcoffset(self, dt):
        return dt.replace(tzinfo=None).astimezone().utcoffset()

    def dst(self, dt):
        return None                     # not known; nothing here needs it

    def tzname(self, dt):
        return dt.replace(tzinfo=None).astimezone().tzname()

    def fromutc(self, dt):
        here = dt.replace(tzinfo=UTC).astimezone()
        naive = here.replace(tzinfo=None)
        second_time_round = naive.replace(fold=0).astimezone().utcoffset() != here.utcoffset()
        return naive.replace(tzinfo=self, fold=int(second_time_round))


_COMPUTER_ZONE = _ComputerZone()


@cache                                  # the rules ask for their zone every round; work each name out once
def rule_timezone(name: str) -> tzinfo:
    """'local' is this computer's clock, a place name follows that place's daylight saving, '+08:00' never changes."""
    if name == 'local':
        return _COMPUTER_ZONE
    offset = _OFFSET.fullmatch(name)
    try:
        if offset:
            if int(offset[3]) >= _MINUTES_IN_HOUR:
                raise ValueError('minutes')
            gap = timedelta(hours=int(offset[2]), minutes=int(offset[3]))
            return timezone(-gap if offset[1] == '-' else gap)
        return ZoneInfo(name)
    except (ValueError, OSError, KeyError):         # an unknown place name is a KeyError
        raise ValueError(f'{name!r} is not a time zone') from None


def _check_timezone(name: str) -> str:
    rule_timezone(name)
    return name


Timezone = Annotated[str, AfterValidator(_check_timezone),
                     _Says('local, a place name like America/Los_Angeles, or an offset like +08:00')]
ObjectState = Literal['present', 'absent', 'unknown']
ChangeKind = Literal['appeared', 'removed', 'moved']
Changes = Annotated[tuple[ChangeKind, ...], Field(min_length=1)]


def _one_or_more(value: Any) -> Any:
    """object: bottle is the same as object: [bottle]."""
    return (value,) if isinstance(value, str) else value


Objects = Annotated[tuple[str, ...], BeforeValidator(_one_or_more), Field(min_length=1)]


class StateCheck(_Section):
    object: str
    is_: ObjectState = Field(alias='is')
    for_: Duration | None = Field(None, alias='for')


class EventCheck(_Section):
    object: Objects
    events: Changes


class NoEventCheck(_Section):
    object: Objects
    events: Changes
    for_: Duration = Field(alias='for')


class HabitCheck(_Section):
    habit: Habit

    @model_validator(mode='after')
    def _the_habit_is_one_the_app_logs(self):
        if self.habit not in HABIT_WORDS:
            raise _Said(f'{self.habit}: nothing logs it yet, so this rule could never see one '
                        f'(habits that are logged: {", ".join(HABIT_WORDS)})')
        return self


class NoHabitCheck(HabitCheck):
    for_: Duration = Field(alias='for')


class StateCond(_Section):
    state: StateCheck


class NoEventCond(_Section):
    no_event: NoEventCheck


class EventCond(_Section):
    event: EventCheck


class NoHabitCond(_Section):
    no_habit: NoHabitCheck


class HabitCond(_Section):
    habit: HabitCheck


class AllCond(_Section):
    all: Annotated[tuple[Condition, ...], Field(min_length=1)]


class AnyCond(_Section):
    any: Annotated[tuple[Condition, ...], Field(min_length=1)]


class NotCond(_Section):
    not_: Condition = Field(alias='not')


_CONDITION_KINDS = {'state': StateCond, 'no_event': NoEventCond, 'event': EventCond,
                    'no_habit': NoHabitCond, 'habit': HabitCond, 'all': AllCond, 'any': AnyCond, 'not': NotCond}
# The kinds that ask what happened after the nudge, so they only work inside success
_AFTER_THE_NUDGE = (EventCond, HabitCond)


def _condition_kind(value: Any) -> str | None:
    """Which kind a condition is (the tag pydantic picks the right section by)."""
    if isinstance(value, dict):
        return next(iter(value), None)
    return next((kind for kind, cls in _CONDITION_KINDS.items() if isinstance(value, cls)), None)


def _one_kind_only(value: Any) -> Any:
    """A condition is a group with exactly one key. Anything else gets one plain sentence, not a pile of union
    errors."""
    if isinstance(value, _Section) or (isinstance(value, dict) and len(value) == 1
                                       and next(iter(value)) in _CONDITION_KINDS):
        return value
    said = f'a condition needs exactly one of {", ".join(_CONDITION_KINDS)}'
    if not isinstance(value, dict):
        raise _Said(f'{said} (got {_got(value)})')
    unknown = [key for key in value if key not in _CONDITION_KINDS]
    if unknown:
        close = difflib.get_close_matches(str(unknown[0]), list(_CONDITION_KINDS), n=1)
        raise _Said(f'{said} (found {unknown[0]}{f", did you mean {close[0]}?" if close else ""})')
    raise _Said(f'{said} (found {" and ".join(map(str, value)) or "nothing"})')


# One of the one-key sections, picked by its key (the checks above give the plain messages).
Condition = Annotated[
    Annotated[StateCond, Tag('state')] | Annotated[NoEventCond, Tag('no_event')] | Annotated[EventCond, Tag('event')]
    | Annotated[NoHabitCond, Tag('no_habit')] | Annotated[HabitCond, Tag('habit')]
    | Annotated[AllCond, Tag('all')] | Annotated[AnyCond, Tag('any')] | Annotated[NotCond, Tag('not')],
    Discriminator(_condition_kind),
    BeforeValidator(_one_kind_only),
]
for _cls in (AllCond, AnyCond, NotCond):
    _cls.model_rebuild()


def _after_the_nudge(condition: Any, negated: bool = False) -> Iterator[tuple[str, bool]]:
    """Every event or habit in the condition, in order, as (its kind, whether it is under a not)."""
    if isinstance(condition, _AFTER_THE_NUDGE):
        yield ('event' if isinstance(condition, EventCond) else 'habit'), negated
    elif isinstance(condition, NotCond):
        yield from _after_the_nudge(condition.not_, True)
    else:
        for inner in getattr(condition, 'all', None) or getattr(condition, 'any', ()):
            yield from _after_the_nudge(inner, negated)


class QuietWindow(_Section):
    start: ClockTime
    end: ClockTime

    @model_validator(mode='after')
    def _check_times(self):
        if parse_hhmm(self.start) == parse_hhmm(self.end):
            raise ValueError(f'start and end are both {self.start}; they must differ (use enabled: false to switch '
                             f'the rule off, or remove quiet_hours)')
        return self


class Success(_Section):
    within: Duration
    when: Condition

    @field_validator('when')
    @classmethod
    def _no_event_under_not(cls, condition):
        kind = next((kind for kind, negated in _after_the_nudge(condition) if negated), None)
        if kind:
            raise _Said(f'{kind} can\'t go inside not (it would count as done before anything happened); to say '
                        f'"this didn\'t happen", use no_{kind}')
        return condition


class RuleConfig(_Section):
    id: str
    enabled: bool
    habit: Habit
    timezone: Timezone
    gate: Condition
    when: Condition
    cooldown: Duration
    max_nudges_per_hour: PositiveInt
    quiet_hours: QuietWindow | None = None
    message: str
    success: Success | None = None

    @field_validator('gate', 'when')
    @classmethod
    def _no_event_before_the_nudge(cls, condition):
        kind = next((kind for kind, _ in _after_the_nudge(condition)), None)
        if kind:
            raise _Said(f'{kind} only works inside success (it asks what happened after the nudge)')
        return condition


class Protocols(_Section):
    schedule: Schedule
    hydration: HydrationRule
    breaks: BreaksRule
    light: LightRule
    phone_free: PhoneFreeRule
    food: FoodRule
    tasks: TasksRule
    rules: tuple[RuleConfig, ...]

    @field_validator('rules')
    @classmethod
    def _ids_are_different(cls, rules):
        seen = set()
        for rule in rules:
            if rule.id in seen:
                raise _Said(f'two rules have the id "{rule.id}"; each rule needs its own id')
            seen.add(rule.id)
        return rules
