"""
The Protocols habit rules (config/protocols.yaml), as plain functions.

Each rule looks at a RuleState (what the camera has seen today) and returns the Nudges whose
reason is true right now, each with the evidence and a ready-made message. Nothing here talks
to the camera or the AI, so every rule is easy to test with made-up numbers.

    nudges = candidate_nudges(state, config.protocols, now)
    ready = ready_nudges(nudges, last_fire, now)       # the ones that may go out now, best first

Every number comes from config/protocols.yaml, including how long to wait before the same
nudge may come again (Nudge.repeat_after_sec). Once-a-day nudges use their habit's own
repeat setting for that wait, so one sent just before midnight isn't sent again straight after.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from desktalk.config import BreaksRule, FoodRule, HydrationRule, LightRule, PhoneFreeRule, Protocols, TasksRule
from desktalk.domain import Nudge
from desktalk.util import at_time_today, clock_text, local_day

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
def hydration_nudges(s: RuleState, cfg: HydrationRule, now: float) -> list[Nudge]:
    if not cfg.enabled or not s.at_desk:
        return []
    wake, bed = s.wake_ts, s.bed_ts
    stop = bed - cfg.stop_before_bed_hours * HOUR
    if now < wake or now >= stop:
        return []          # asleep, or close enough to bed that more water means a wake-up later
    out = []
    drunk = float(s.water_ml)
    on_waking = cfg.on_waking_ml
    if now - wake <= cfg.on_waking_window_min * MIN and drunk < on_waking * cfg.on_waking_done_share:
        out.append(Nudge(
            'hydration', 'hydration.morning',
            evidence=f'It is {_mins(now - wake)} minutes after your wake time and almost no water has been logged yet.',
            text=(f"Morning! The book's first habit of the day: about {on_waking / 1000:.1f} L (18–24 oz) of water "
                  f"right after waking, before coffee. Had a glass yet?"),
            tip='Drink 18 to 24 ounces (about 600 ml) of water upon waking, before coffee.',
            repeat_after_sec=cfg.remind_after_min * MIN, once_per_day=True))

    window = cfg.goal_window_hours * HOUR
    goal = cfg.daily_goal_ml
    expected = goal * min(1.0, max(0.0, (now - wake) / window))
    since = max(x for x in (s.last_sip_ts, s.session_start, wake) if x)
    quiet = now - since
    behind = expected - drunk
    if quiet >= cfg.remind_after_min * MIN and behind >= cfg.behind_by_ml:
        m = _mins(quiet)
        progress = f'about {_liters(drunk)} so far, and the pace for now is {_liters(expected)}'
        if s.drink_on_desk:
            text = f"Nobody has picked me up in {m} minutes. I'm still full! You're at {progress}. Have a sip?"
            evidence = f'The cup on the desk has not been picked up in {m} minutes; {progress}.'
        else:
            text = f"I haven't seen you drink anything in {m} minutes ({progress}). Want to grab some water?"
            evidence = f'No drink has been seen or logged in {m} minutes; {progress}.'
        out.append(Nudge('hydration', 'hydration.pace', evidence=evidence, text=text,
                         tip=(f'Aim for about {goal / 1000:.1f} L (80 oz) across the first ten hours after waking, '
                              f'roughly a glass every waking hour; earlier in the day is better.'),
                         repeat_after_sec=cfg.remind_after_min * MIN))
    return out


# ════════════════════════════════════════════════════════════════════
#  2. Breaks (Exercise Protocol 1, Learning Protocol 5)
# ════════════════════════════════════════════════════════════════════
def break_nudges(s: RuleState, cfg: BreaksRule, now: float) -> list[Nudge]:
    if not cfg.enabled:
        return []
    out = []
    phone_note = ' Leave the phone here.' if cfg.no_phone_on_breaks else ''
    limit = cfg.sit_limit_min * MIN
    if s.at_desk and s.sit_since and now - s.sit_since >= limit:
        m = _mins(now - s.sit_since)
        brk = int(cfg.break_min)
        out.append(Nudge(
            'breaks', 'breaks.sit',
            evidence=f'The user has been at the desk for {m} minutes without leaving.',
            text=(f"You've been sitting for {m} minutes. Time for a {brk}-minute walk; "
                  f"I'll count it when I see you leave the desk.{phone_note}"),
            tip=(f'Walk or pace for at least {brk} minutes for every {int(limit // MIN)} minutes of sitting. '
                 f'During breaks, do nothing on purpose: no checking your phone.'),
            repeat_after_sec=cfg.repeat_after_min * MIN,
            speakers=('breaks', 'screens')))
    meal_done = s.meal_done_ts
    if (cfg.walk_after_meal and meal_done and 0 <= now - meal_done <= cfg.walk_window_after_meal_min * MIN
            and s.at_desk):
        food = s.meal_name or 'food'
        out.append(Nudge(
            'breaks', f'breaks.meal.{int(meal_done)}',
            evidence=f'The {food} that was on the desk is gone, so a meal or snack just ended.',
            text=(f"That {food} is gone! A 5 to 10 minute walk right after eating is one of the book's easy wins."
                  f"{phone_note}"),
            tip='Walk for five to ten minutes after meals, when the benefits are amplified.',
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


def _light_words(s: RuleState) -> str:
    if s.lux is not None:
        return f'about {int(s.lux)} lux'
    if s.brightness is not None:
        return f'picture brightness {int(s.brightness)} of 255'
    return 'bright'


def light_nudges(s: RuleState, cfg: LightRule, now: float) -> list[Nudge]:
    if not cfg.enabled or not s.at_desk:
        return []
    out = []
    wake, bed = s.wake_ts, s.bed_ts
    sunset = s.sunset_ts or (bed - cfg.sunset_guess_before_bed_hours * HOUR)
    mm = cfg.morning_minutes
    clear, partly, over = int(mm.clear), int(mm.partly_cloudy), int(mm.overcast)
    repeat = cfg.repeat_after_min * MIN

    # Morning sunlight, in the first hour after waking.
    window = cfg.morning_window_min * MIN
    if wake <= now <= wake + window + cfg.morning_grace_min * MIN and not s.sunlight_ts:
        out.append(Nudge(
            'light', 'light.morning',
            evidence=(f'You sat down {_mins(now - wake)} minutes after your wake time '
                      f'and no morning sunlight is logged.'),
            text=(f"Good morning! Have you been outside yet? Sunlight in your eyes in the first hour after waking: "
                  f"about {clear} min if it's clear, {partly} if partly cloudy, {over} if overcast. "
                  f"A window blocks most of it. Log it on the Habits page when you're back."),
            tip=(f'View sunlight outside within 30 to 60 minutes of waking: {clear} min on clear days, '
                 f'{partly} partly cloudy, {over} overcast. Not through a window, no sunglasses. '
                 f'Never stare at the sun.'),
            repeat_after_sec=repeat, once_per_day=True, speakers=('light',)))

    # Late-afternoon sunlight, before sunset.
    if cfg.afternoon and not s.afternoon_light_ts:
        start = sunset - cfg.afternoon_start_min_before_sunset * MIN
        end = sunset - cfg.afternoon_end_min_before_sunset * MIN
        if start <= now <= end:
            out.append(Nudge(
                'light', 'light.afternoon',
                evidence=f'Sunset is at {clock_text(sunset)}, {_mins(sunset - now)} minutes from now.',
                text=(f"Sunset is at {clock_text(sunset)}. Ten or fifteen minutes outside before then, facing west, "
                      f"helps protect your sleep from screen light tonight."),
                tip='Get 10 to 15 minutes of outdoor light in the late afternoon, before the sun sets.',
                repeat_after_sec=repeat, once_per_day=True, speakers=('light',)))

    # Evening: dim the lights after sunset / in the hour before bed.
    evening_start = min(sunset, bed - cfg.evening_dim_before_bed_min * MIN)
    bright_for = cfg.evening_bright_min
    if (now >= evening_start and s.bright_since and now - s.bright_since >= bright_for * MIN
            and is_bright(s.lux, s.brightness, cfg)):
        to_bed = bed - now
        when = f'Bed is in {_mins(to_bed)} minutes' if 0 < to_bed <= cfg.bed_soon_min * MIN else 'The sun has set'
        out.append(Nudge(
            'light', 'light.evening',
            evidence=f'{when} and the room is bright ({_light_words(s)}) for over {bright_for:g} minutes.',
            text=(f"{when} and it's really bright in here. Dim the lights or switch to a small lamp; "
                  f"bright light at night pushes your sleep later."),
            tip=('After sunset, and especially in the last hour before bed, use only as much light as you need; '
                 'dim, warm lamps are best.'),
            repeat_after_sec=repeat, speakers=('light',)))

    # Daytime: dim room.
    margin = cfg.day_dim_margin_min * MIN
    dim_since = s.dim_since
    if (wake + margin <= now <= sunset - margin and dim_since and now - dim_since >= cfg.day_dim_min * MIN
            and is_dim(s.lux, s.brightness, cfg)):
        out.append(Nudge(
            'light', 'light.daytime_dim',
            evidence=(f'It is daytime and the room has been dim ({_light_words(s)}) '
                      f'for {_mins(now - dim_since)} minutes.'),
            text=("It's daytime but it's pretty dim in here. Open the blinds or turn on a bright light; "
                  "bright days help you stay alert now and sleep better tonight."),
            tip=('Bright light during the day (ideally outdoor light) supports alertness '
                 'and a healthy sleep-wake rhythm.'),
            repeat_after_sec=repeat, speakers=('light',)))
    return out


def screen_nudges(s: RuleState, cfg: LightRule, now: float) -> list[Nudge]:
    """Sleep Protocol 14: no screens within an hour of bedtime."""
    if not cfg.enabled or not s.at_desk or not s.screen_on_desk:
        return []
    bed = s.bed_ts
    before = cfg.screens_off_before_bed_min * MIN
    if bed - before <= now < bed + cfg.screens_until_after_bed_hours * HOUR:
        left = bed - now
        lead = f'Bed is at {clock_text(bed)}, {_mins(left)} minutes from now' if left > 0 else 'It is past your bedtime'
        return [Nudge(
            'screens', 'screens.bedtime',
            evidence=f'{lead}, and the computer is still on the desk with you at it.',
            text=(f"{lead}. The book says no screens in the last hour before sleep. "
                  f"If you really need me, turn my brightness way down."),
            tip='Avoid screens within an hour of bedtime; if you must use one, dim it and use the red / night mode.',
            repeat_after_sec=cfg.repeat_after_min * MIN, once_per_day=True, speakers=('screens',))]
    return []


# ════════════════════════════════════════════════════════════════════
#  4. Phone-free time (Learning Protocol 1, Sleep Protocol 14)
# ════════════════════════════════════════════════════════════════════
def phone_nudges(s: RuleState, cfg: PhoneFreeRule, now: float) -> list[Nudge]:
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
        return [Nudge(
            'phone_free', 'phone.bedtime',
            evidence=f'Bed is at {clock_text(bed)} and the phone has been on the desk for {_mins(on_for)} minutes.',
            text=(f"Psst, it's the last hour before bed ({clock_text(bed)}). How about I go charge in another room? "
                  f"Screens this late make it harder to fall asleep."),
            tip='Avoid phones and other screens within an hour of bedtime.',
            repeat_after_sec=repeat, once_per_day=True, speakers=('phone_free',))]
    if on_for >= cfg.nudge_after_min * MIN:
        m = _mins(on_for)
        return [Nudge(
            'phone_free', 'phone.focus',
            evidence=f'The phone has been on the desk for {m} minutes while the user is working.',
            text=(f"I've been sitting next to you for {m} minutes. Even a phone in sight pulls at your focus. "
                  f"Put me in another room for a while?"),
            tip=('Put your phone away while you focus. Even having it in the same room, in a bag, '
                 'reduces your ability to focus.'),
            repeat_after_sec=repeat, speakers=('phone_free', 'tasks'))]
    return []


# ════════════════════════════════════════════════════════════════════
#  Kept from the original Desk Talk reminders
# ════════════════════════════════════════════════════════════════════
def food_nudges(s: RuleState, cfg: FoodRule, now: float) -> list[Nudge]:
    since = s.food_since
    if not cfg.enabled or not since:
        return []
    if now - since >= cfg.remind_after_min * MIN:
        name = s.food_name or 'snack'
        m = _mins(now - since)
        return [Nudge('food', f'food.sitting.{int(since)}',
                      evidence=f'A {name} has been on the desk for {m} minutes.',
                      text=(f'This {name} has been next to you for {m} minutes. '
                            f'Eat it or move it away from the keyboard?'),
                      tip='Keep food away from the keyboard and papers.',
                      repeat_after_sec=cfg.repeat_after_hours * HOUR)]
    return []


def task_nudges(s: RuleState, cfg: TasksRule, now: float) -> list[Nudge]:
    todos = [t for t in s.open_todos if t]
    start = s.session_start
    if not cfg.enabled or not todos or not start or not s.at_desk:
        return []
    every = cfg.remind_after_min * MIN
    if now - start < every:
        return []
    n = len(todos)
    plural = 's' if n != 1 else ''
    first = todos[0][:cfg.name_shown_chars]
    return [Nudge('tasks', 'tasks.open',
                  evidence=f'The user has {n} open task{plural} on their list, starting with "{first}".',
                  text=(f'You still have {n} open task{plural}, like "{first}". '
                        f'Want to pick one for the next 30 minutes?'),
                  tip='Pick one task and focus on it for a set block of time.',
                  repeat_after_sec=every)]


def candidate_nudges(state: RuleState, protocols: Protocols, now: float) -> list[Nudge]:
    """Every nudge whose reason is true right now (before the "not again so soon" waits)."""
    return [*hydration_nudges(state, protocols.hydration, now),
            *break_nudges(state, protocols.breaks, now),
            *light_nudges(state, protocols.light, now),
            *screen_nudges(state, protocols.light, now),
            *phone_nudges(state, protocols.phone_free, now),
            *food_nudges(state, protocols.food, now),
            *task_nudges(state, protocols.tasks, now)]


def ready_nudges(nudges: Sequence[Nudge], last_fire: Mapping[str, float], now: float) -> list[Nudge]:
    """The nudges that may go out now (not said too recently), least recently heard habit first.
    `last_fire` maps rule key → when it was last sent, and 'habit:<name>' → the same for the habit."""
    today = local_day(now)
    ready = []
    for n in nudges:
        last = last_fire.get(n.key)
        if last:
            if n.once_per_day and local_day(last) == today:
                continue
            if now - last < n.repeat_after_sec:
                continue
        ready.append(n)
    return sorted(ready, key=lambda n: last_fire.get(f'habit:{n.habit}', 0) or 0)

