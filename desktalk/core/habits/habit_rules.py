"""
The Protocols habit rules (config/protocols.yaml), as plain functions.

Each rule looks at a RuleState (what the camera has seen today) and returns the Nudges whose
reason is true right now, each with the evidence and a ready-made message. Nothing here talks
to the camera or the AI, so every rule is easy to test with made-up numbers.

    nudges = candidate_nudges(state, config.protocols, now, config.prompts.habits)
    ready = ready_nudges(nudges, last_fire, now, day_of=clock.local_day)   # may go out now, best first

The words come from config/prompts.yaml (habits:). Every number comes from config/protocols.yaml,
including how long to wait before the same nudge may come again (Nudge.repeat_after_sec). Once-a-day
nudges use their habit's own repeat setting for that wait, so one sent just before midnight isn't sent
again straight after.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from desktalk.config import BreaksRule, FoodRule, HydrationRule, LightRule, PhoneFreeRule, Protocols, TasksRule
from desktalk.config.prompts import HabitNudges
from desktalk.domain import Nudge
from desktalk.util import at_time_today, clock_text

MIN = 60
HOUR = 3600
DAY_SEC = 86400


@dataclass(frozen=True, kw_only=True)
class RuleState:
    """What the rules look at: a snapshot of today, made by the habit tracker. Times are seconds since 1970."""

    wake_ts: float                          # today's wake time and bedtime (see day_frame)
    bed_ts: float
    sunset_ts: float | None = None          # None = guess it from the bedtime
    at_desk: bool = False                   # someone is at the desk right now
    session_start: float | None = None      # when the camera started watching
    water_ml: float = 0
    last_sip_ts: float | None = None
    drink_on_desk: bool = False
    sit_since: float | None = None
    meal_done_ts: float | None = None       # food that sat on the desk a while just went away
    meal_name: str = ''
    food_since: float | None = None         # food on the desk right now, since when
    food_name: str = ''
    phone_on_desk_since: float | None = None
    phone_counts: bool = True               # False when the phone is the camera
    sunlight_ts: float | None = None        # morning sunlight logged
    afternoon_light_ts: float | None = None
    lux: float | None = None                # from the Pi camera module, if it has one
    brightness: float | None = None         # 0-255 picture brightness
    bright_since: float | None = None
    dim_since: float | None = None
    screen_on_desk: bool = False
    open_todos: tuple[str, ...] = ()


def day_frame(now: float, wake: str, bed: str) -> tuple[float, float]:
    """(wake_ts, bed_ts) of the waking day that contains `now` (bedtime may be after midnight)."""
    wake_ts = at_time_today(wake, now)
    if wake_ts > now:                     # past midnight but before waking: still "yesterday"
        wake_ts -= DAY_SEC
    bed_ts = at_time_today(bed, wake_ts)
    if bed_ts <= wake_ts:
        bed_ts += DAY_SEC
    return wake_ts, bed_ts


def _mins(seconds: float) -> int:
    return int(max(0, seconds) // 60)


def _liters(ml: float) -> str:
    return f'{ml / 1000:.1f} L'


# ════════════════════════════════════════════════════════════════════
#  1. Hydration (Nutrition Protocol 2)
# ════════════════════════════════════════════════════════════════════
def hydration_nudges(s: RuleState, cfg: HydrationRule, now: float, words: HabitNudges) -> list[Nudge]:
    if not cfg.enabled or not s.at_desk:
        return []
    wake, bed = s.wake_ts, s.bed_ts
    stop = bed - cfg.stop_before_bed_hours * HOUR
    if now < wake or now >= stop:
        return []          # asleep, or close enough to bed that more water means a wake-up later
    out = []
    drunk = float(s.water_ml)
    if now - wake <= cfg.on_waking_window_min * MIN and drunk < cfg.on_waking_ml * cfg.on_waking_done_share:
        w = words.hydration_morning
        out.append(Nudge(
            'hydration', 'hydration.morning', evidence=w.evidence.format(minutes=_mins(now - wake)), text=w.text,
            tip=w.tip, repeat_after_sec=cfg.remind_after_min * MIN, once_per_day=True))

    window = cfg.goal_window_hours * HOUR
    goal = cfg.daily_goal_ml
    expected = goal * min(1.0, max(0.0, (now - wake) / window))
    since = max(x for x in (s.last_sip_ts, s.session_start, wake) if x)
    quiet = now - since
    behind = expected - drunk
    if quiet >= cfg.remind_after_min * MIN and behind >= cfg.behind_by_ml:
        blanks = {'minutes': _mins(quiet),
                  'progress': words.water_progress.format(drunk=_liters(drunk), pace=_liters(expected))}
        said = words.hydration_pace_cup if s.drink_on_desk else words.hydration_pace
        out.append(Nudge('hydration', 'hydration.pace', evidence=said.evidence.format(**blanks),
                         text=said.text.format(**blanks), tip=words.hydration_pace.tip,
                         repeat_after_sec=cfg.remind_after_min * MIN))
    return out


# ════════════════════════════════════════════════════════════════════
#  2. Breaks (Exercise Protocol 1, Learning Protocol 5)
# ════════════════════════════════════════════════════════════════════
def break_nudges(s: RuleState, cfg: BreaksRule, now: float, words: HabitNudges) -> list[Nudge]:
    if not cfg.enabled:
        return []
    out = []
    phone_note = words.phone_note if cfg.no_phone_on_breaks else ''
    limit = cfg.sit_limit_min * MIN
    if s.at_desk and s.sit_since and now - s.sit_since >= limit:
        m = _mins(now - s.sit_since)
        w = words.breaks_sit
        out.append(Nudge(
            'breaks', 'breaks.sit', evidence=w.evidence.format(minutes=m),
            text=w.text.format(minutes=m, phone_note=phone_note), tip=w.tip,
            repeat_after_sec=cfg.repeat_after_min * MIN, speakers=('breaks', 'screens')))
    meal_done = s.meal_done_ts
    if (cfg.walk_after_meal and meal_done and 0 <= now - meal_done <= cfg.walk_window_after_meal_min * MIN
            and s.at_desk):
        food = s.meal_name or words.unknown_meal
        w = words.breaks_meal
        out.append(Nudge(
            'breaks', f'breaks.meal.{int(meal_done)}', evidence=w.evidence.format(food=food),
            text=w.text.format(food=food, phone_note=phone_note), tip=w.tip,
            repeat_after_sec=cfg.walk_repeat_after_hours * HOUR, speakers=('breaks', 'food')))
    return out


# ════════════════════════════════════════════════════════════════════
#  3. Light, day and night (Sleep 1-3, Light 1-3, Sleep 14)
# ════════════════════════════════════════════════════════════════════
def is_bright(lux: float | None, brightness: float | None, cfg: LightRule) -> bool:
    """Bright enough to push sleep later at night. Lux when the camera gives it, else picture brightness."""
    if lux is not None:
        return lux >= cfg.night_bright_lux
    return brightness is not None and brightness >= cfg.night_bright_level


def is_dim(lux: float | None, brightness: float | None, cfg: LightRule) -> bool:
    """Too dim for the daytime."""
    if lux is not None:
        return lux < cfg.day_dim_lux
    return brightness is not None and brightness < cfg.day_dim_level


def _light_words(s: RuleState, words: HabitNudges) -> str:
    if s.lux is not None:
        return words.light_lux.format(lux=int(s.lux))
    if s.brightness is not None:
        return words.light_brightness.format(brightness=int(s.brightness))
    return words.light_bright


def light_nudges(s: RuleState, cfg: LightRule, now: float, words: HabitNudges) -> list[Nudge]:
    if not cfg.enabled or not s.at_desk:
        return []
    out = []
    wake, bed = s.wake_ts, s.bed_ts
    sunset = s.sunset_ts or (bed - cfg.sunset_guess_before_bed_hours * HOUR)
    repeat = cfg.repeat_after_min * MIN

    # Morning sunlight, in the first hour after waking.
    window = cfg.morning_window_min * MIN
    if wake <= now <= wake + window + cfg.morning_grace_min * MIN and not s.sunlight_ts:
        w = words.light_morning
        out.append(Nudge(
            'light', 'light.morning', evidence=w.evidence.format(minutes=_mins(now - wake)), text=w.text, tip=w.tip,
            repeat_after_sec=repeat, once_per_day=True, speakers=('light',)))

    # Late-afternoon sunlight, before sunset.
    if cfg.afternoon and not s.afternoon_light_ts:
        start = sunset - cfg.afternoon_start_min_before_sunset * MIN
        end = sunset - cfg.afternoon_end_min_before_sunset * MIN
        if start <= now <= end:
            w = words.light_afternoon
            out.append(Nudge(
                'light', 'light.afternoon',
                evidence=w.evidence.format(sunset=clock_text(sunset), minutes=_mins(sunset - now)),
                text=w.text.format(sunset=clock_text(sunset)), tip=w.tip,
                repeat_after_sec=repeat, once_per_day=True, speakers=('light',)))

    # Evening: dim the lights after sunset / in the hour before bed.
    evening_start = min(sunset, bed - cfg.evening_dim_before_bed_min * MIN)
    if (now >= evening_start and s.bright_since and now - s.bright_since >= cfg.evening_bright_min * MIN
            and is_bright(s.lux, s.brightness, cfg)):
        to_bed = bed - now
        when = (words.bed_soon.format(minutes=_mins(to_bed)) if 0 < to_bed <= cfg.bed_soon_min * MIN
                else words.sun_has_set)
        w = words.light_evening
        out.append(Nudge(
            'light', 'light.evening', evidence=w.evidence.format(when=when, light=_light_words(s, words)),
            text=w.text.format(when=when), tip=w.tip, repeat_after_sec=repeat, speakers=('light',)))

    # Daytime: dim room.
    margin = cfg.day_dim_margin_min * MIN
    dim_since = s.dim_since
    if (wake + margin <= now <= sunset - margin and dim_since and now - dim_since >= cfg.day_dim_min * MIN
            and is_dim(s.lux, s.brightness, cfg)):
        w = words.light_daytime_dim
        out.append(Nudge(
            'light', 'light.daytime_dim',
            evidence=w.evidence.format(light=_light_words(s, words), minutes=_mins(now - dim_since)),
            text=w.text, tip=w.tip, repeat_after_sec=repeat, speakers=('light',)))
    return out


def screen_nudges(s: RuleState, cfg: LightRule, now: float, words: HabitNudges) -> list[Nudge]:
    """Sleep Protocol 14: no screens within an hour of bedtime."""
    if not cfg.enabled or not s.at_desk or not s.screen_on_desk:
        return []
    bed = s.bed_ts
    before = cfg.screens_off_before_bed_min * MIN
    if bed - before <= now < bed + cfg.screens_until_after_bed_hours * HOUR:
        left = bed - now
        lead = (words.bed_at.format(bed=clock_text(bed), minutes=_mins(left)) if left > 0
                else words.past_bedtime)
        w = words.screens_bedtime
        return [Nudge(
            'screens', 'screens.bedtime', evidence=w.evidence.format(lead=lead), text=w.text.format(lead=lead),
            tip=w.tip, repeat_after_sec=cfg.repeat_after_min * MIN, once_per_day=True, speakers=('screens',))]
    return []


# ════════════════════════════════════════════════════════════════════
#  4. Phone-free time (Learning Protocol 1, Sleep Protocol 14)
# ════════════════════════════════════════════════════════════════════
def phone_nudges(s: RuleState, cfg: PhoneFreeRule, now: float, words: HabitNudges) -> list[Nudge]:
    if not cfg.enabled or not s.at_desk or not s.phone_counts:
        return []
    since = s.phone_on_desk_since
    if not since:
        return []
    on_for = now - since
    bed = s.bed_ts
    repeat = cfg.repeat_after_min * MIN
    if (bed - cfg.before_bed_min * MIN <= now < bed + cfg.before_bed_until_after_bed_hours * HOUR
            and on_for >= cfg.before_bed_on_desk_min * MIN):
        w = words.phone_bedtime
        return [Nudge(
            'phone_free', 'phone.bedtime', evidence=w.evidence.format(bed=clock_text(bed), minutes=_mins(on_for)),
            text=w.text.format(bed=clock_text(bed)), tip=w.tip,
            repeat_after_sec=repeat, once_per_day=True, speakers=('phone_free',))]
    if on_for >= cfg.nudge_after_min * MIN:
        m = _mins(on_for)
        w = words.phone_focus
        return [Nudge(
            'phone_free', 'phone.focus', evidence=w.evidence.format(minutes=m), text=w.text.format(minutes=m),
            tip=w.tip, repeat_after_sec=repeat, speakers=('phone_free', 'tasks'))]
    return []


# ════════════════════════════════════════════════════════════════════
#  Kept from the original Desk Talk reminders
# ════════════════════════════════════════════════════════════════════
def food_nudges(s: RuleState, cfg: FoodRule, now: float, words: HabitNudges) -> list[Nudge]:
    since = s.food_since
    if not cfg.enabled or not since:
        return []
    if now - since >= cfg.remind_after_min * MIN:
        blanks = {'food': s.food_name or words.unknown_snack, 'minutes': _mins(now - since)}
        w = words.food_sitting
        return [Nudge('food', f'food.sitting.{int(since)}', evidence=w.evidence.format(**blanks),
                      text=w.text.format(**blanks), tip=w.tip, repeat_after_sec=cfg.repeat_after_hours * HOUR)]
    return []


def task_nudges(s: RuleState, cfg: TasksRule, now: float, words: HabitNudges) -> list[Nudge]:
    todos = [t for t in s.open_todos if t]
    start = s.session_start
    if not cfg.enabled or not todos or not start or not s.at_desk:
        return []
    every = cfg.remind_after_min * MIN
    if now - start < every:
        return []
    n = len(todos)
    blanks = {'count': n, 'plural': 's' if n != 1 else '', 'first': todos[0][:cfg.name_shown_chars]}
    w = words.tasks_open
    return [Nudge('tasks', 'tasks.open', evidence=w.evidence.format(**blanks), text=w.text.format(**blanks),
                  tip=w.tip, repeat_after_sec=every)]


def candidate_nudges(state: RuleState, protocols: Protocols, now: float, words: HabitNudges) -> list[Nudge]:
    """Every nudge whose reason is true right now (before the "not again so soon" waits)."""
    return [*hydration_nudges(state, protocols.hydration, now, words),
            *break_nudges(state, protocols.breaks, now, words),
            *light_nudges(state, protocols.light, now, words),
            *screen_nudges(state, protocols.light, now, words),
            *phone_nudges(state, protocols.phone_free, now, words),
            *food_nudges(state, protocols.food, now, words),
            *task_nudges(state, protocols.tasks, now, words)]


def ready_nudges(nudges: Sequence[Nudge], last_fire: Mapping[str, float], now: float, *,
                 day_of: Callable[[float], str]) -> list[Nudge]:
    """The nudges that may go out now (not said too recently), least recently heard habit first.
    `last_fire` maps rule key → when it was last sent, and 'habit:<name>' → the same for the habit.
    `day_of` gives the local day of a time (Clock.local_day), for the once-a-day nudges."""
    today = day_of(now)
    ready = []
    for n in nudges:
        last = last_fire.get(n.key)
        if last:
            if n.once_per_day and day_of(last) == today:
                continue
            if now - last < n.repeat_after_sec:
                continue
        ready.append(n)
    return sorted(ready, key=lambda n: last_fire.get(f'habit:{n.habit}', 0) or 0)
