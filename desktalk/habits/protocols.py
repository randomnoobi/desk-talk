"""
The Protocols habit rules (config/protocols.yaml), as plain functions.

Each rule looks at a snapshot of what the camera has seen today and returns a
Nudge (with the evidence and a ready-made message) or None. Nothing here talks
to the camera or the AI, so every rule is easy to test with made-up numbers.

    nudges = candidate_nudges(state, protocols_cfg, now)
    nudge = pick_nudge(nudges, last_fire, now)

`state` keys (all optional):
    at_desk, session_start, wake_ts, bed_ts, sunset_ts,
    water_ml, sips, last_sip_ts, drink_on_desk,
    sit_since, last_break_ts, meal_done_ts, meal_name,
    food_since, food_name, phone_on_desk_since, phone_counts,
    sunlight_ts, afternoon_light_ts, lux, brightness, bright_since, dim_since,
    screen_on_desk, open_todos
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..util import at_time_today, clock_text, local_day

MIN = 60


@dataclass
class Nudge:
    habit: str                 # hydration | breaks | light | phone_free | screens | food | tasks
    key: str                   # rule id, used for "don't repeat too soon"
    evidence: str              # what the camera saw (the AI must mention it)
    text: str                  # ready-made message (used without AI, and by Lens)
    tip: str = ''              # what the book recommends (given to the AI)
    repeat_after_sec: float = 30 * MIN
    once_per_day: bool = False
    speakers: tuple = field(default_factory=tuple)   # habits whose agents may say it

    def __post_init__(self):
        if not self.speakers:
            self.speakers = (self.habit,)


def day_frame(now: float, wake: str, bed: str) -> tuple[float, float]:
    """(wake_ts, bed_ts) of the waking day that contains `now` (bedtime may be after midnight)."""
    wake_ts = at_time_today(wake, now)
    if wake_ts > now:                     # past midnight but before waking: still "yesterday"
        wake_ts -= 86400
    bed_ts = at_time_today(bed, wake_ts)
    if bed_ts <= wake_ts:
        bed_ts += 86400
    return wake_ts, bed_ts


def _mins(seconds: float) -> int:
    return int(max(0, seconds) // 60)


def _liters(ml: float) -> str:
    return f'{ml / 1000:.1f} L'


# ════════════════════════════════════════════════════════════════════
#  1. Hydration (Nutrition Protocol 2)
# ════════════════════════════════════════════════════════════════════
def hydration_nudges(s: dict, cfg: dict, now: float) -> list[Nudge]:
    if not cfg.get('enabled', True) or not s.get('at_desk'):
        return []
    wake, bed = s['wake_ts'], s['bed_ts']
    stop = bed - float(cfg.get('stop_before_bed_hours', 2)) * 3600
    if now < wake or now >= stop:
        return []          # asleep, or close enough to bed that more water means a wake-up later
    out = []
    drunk = float(s.get('water_ml', 0))
    on_waking = float(cfg.get('on_waking_ml', 600))
    if now - wake <= 60 * MIN and drunk < on_waking / 2:
        out.append(Nudge(
            'hydration', 'hydration.morning',
            evidence=f'It is {_mins(now - wake)} minutes after your wake time and almost no water has been logged yet.',
            text=(f"Morning! The book's first habit of the day: about {on_waking / 1000:.1f} L (18–24 oz) of water "
                  f"right after waking, before coffee. Had a glass yet?"),
            tip='Drink 18 to 24 ounces (about 600 ml) of water upon waking, before coffee.',
            once_per_day=True))

    window = float(cfg.get('goal_window_hours', 10)) * 3600
    goal = float(cfg.get('daily_goal_ml', 2400))
    expected = goal * min(1.0, max(0.0, (now - wake) / window))
    since = max(x for x in (s.get('last_sip_ts'), s.get('session_start'), wake) if x)
    quiet = now - since
    behind = expected - drunk
    if quiet >= float(cfg.get('remind_after_min', 45)) * MIN and behind >= float(cfg.get('behind_by_ml', 300)):
        m = _mins(quiet)
        progress = f'about {_liters(drunk)} so far, and the pace for now is {_liters(expected)}'
        if s.get('drink_on_desk'):
            text = (f"Nobody has picked me up in {m} minutes. I'm still full! You're at {progress}. Have a sip?")
            evidence = f'The cup on the desk has not been picked up in {m} minutes; {progress}.'
        else:
            text = (f"I haven't seen you drink anything in {m} minutes ({progress}). Want to grab some water?")
            evidence = f'No drink has been seen or logged in {m} minutes; {progress}.'
        out.append(Nudge('hydration', 'hydration.pace', evidence=evidence, text=text,
                         tip=(f'Aim for about {goal / 1000:.1f} L (80 oz) across the first ten hours after waking, '
                              f'roughly a glass every waking hour; earlier in the day is better.'),
                         repeat_after_sec=float(cfg.get('remind_after_min', 45)) * MIN))
    return out


# ════════════════════════════════════════════════════════════════════
#  2. Breaks (Exercise Protocol 1, Learning Protocol 5)
# ════════════════════════════════════════════════════════════════════
def break_nudges(s: dict, cfg: dict, now: float) -> list[Nudge]:
    if not cfg.get('enabled', True):
        return []
    out = []
    phone_note = ' Leave the phone here.' if cfg.get('no_phone_on_breaks', True) else ''
    sit_since = s.get('sit_since')
    limit = float(cfg.get('sit_limit_min', 30)) * MIN
    if s.get('at_desk') and sit_since and now - sit_since >= limit:
        m = _mins(now - sit_since)
        brk = int(cfg.get('break_min', 5))
        out.append(Nudge(
            'breaks', 'breaks.sit',
            evidence=f'The user has been at the desk for {m} minutes without leaving.',
            text=(f"You've been sitting for {m} minutes. Time for a {brk}-minute walk; "
                  f"I'll count it when I see you leave the desk.{phone_note}"),
            tip=(f'Walk or pace for at least {brk} minutes for every {int(limit // MIN)} minutes of sitting. '
                 f'During breaks, do nothing on purpose: no checking your phone.'),
            repeat_after_sec=float(cfg.get('repeat_after_min', 15)) * MIN,
            speakers=('breaks', 'screens')))
    meal_done = s.get('meal_done_ts')
    if cfg.get('walk_after_meal', True) and meal_done and 0 <= now - meal_done <= 10 * MIN and s.get('at_desk'):
        food = s.get('meal_name') or 'food'
        out.append(Nudge(
            'breaks', f'breaks.meal.{int(meal_done)}',
            evidence=f'The {food} that was on the desk is gone, so a meal or snack just ended.',
            text=f"That {food} is gone! A 5 to 10 minute walk right after eating is one of the book's easy wins.{phone_note}",
            tip='Walk for five to ten minutes after meals, when the benefits are amplified.',
            repeat_after_sec=6 * 3600, speakers=('breaks', 'food')))
    return out


# ════════════════════════════════════════════════════════════════════
#  3. Light, day and night (Sleep 1-3, Light 1-3, Sleep 14)
# ════════════════════════════════════════════════════════════════════
def _is_bright(s, cfg) -> bool:
    lux = s.get('lux')
    if lux is not None:
        return lux >= float(cfg.get('night_bright_lux', 120))
    b = s.get('brightness')
    return b is not None and b >= float(cfg.get('night_bright_level', 150))


def _is_dim(s, cfg) -> bool:
    lux = s.get('lux')
    if lux is not None:
        return lux < float(cfg.get('day_dim_lux', 80))
    b = s.get('brightness')
    return b is not None and b < float(cfg.get('day_dim_level', 50))


def _light_words(s) -> str:
    if s.get('lux') is not None:
        return f'about {int(s["lux"])} lux'
    if s.get('brightness') is not None:
        return f'picture brightness {int(s["brightness"])} of 255'
    return 'bright'


def light_nudges(s: dict, cfg: dict, now: float) -> list[Nudge]:
    if not cfg.get('enabled', True) or not s.get('at_desk'):
        return []
    out = []
    wake, bed, sunset = s['wake_ts'], s['bed_ts'], s.get('sunset_ts') or (s['bed_ts'] - 4 * 3600)
    mm = cfg.get('morning_minutes') or {}
    clear, partly, over = mm.get('clear', 10), mm.get('partly_cloudy', 20), mm.get('overcast', 30)

    # Morning sunlight, in the first hour after waking.
    window = float(cfg.get('morning_window_min', 60)) * MIN
    if wake <= now <= wake + window + 30 * MIN and not s.get('sunlight_ts'):
        out.append(Nudge(
            'light', 'light.morning',
            evidence=f'You sat down {_mins(now - wake)} minutes after your wake time and no morning sunlight is logged.',
            text=(f"Good morning! Have you been outside yet? Sunlight in your eyes in the first hour after waking: "
                  f"about {clear} min if it's clear, {partly} if partly cloudy, {over} if overcast. "
                  f"A window blocks most of it. Log it on the Habits page when you're back."),
            tip=(f'View sunlight outside within 30 to 60 minutes of waking: {clear} min on clear days, '
                 f'{partly} partly cloudy, {over} overcast. Not through a window, no sunglasses. Never stare at the sun.'),
            once_per_day=True, speakers=('light',)))

    # Late-afternoon sunlight, before sunset.
    if cfg.get('afternoon', True) and not s.get('afternoon_light_ts'):
        start = sunset - float(cfg.get('afternoon_start_min_before_sunset', 120)) * MIN
        end = sunset - float(cfg.get('afternoon_end_min_before_sunset', 20)) * MIN
        if start <= now <= end:
            out.append(Nudge(
                'light', 'light.afternoon',
                evidence=f'Sunset is at {clock_text(sunset)}, {_mins(sunset - now)} minutes from now.',
                text=(f"Sunset is at {clock_text(sunset)}. Ten or fifteen minutes outside before then, facing west, "
                      f"helps protect your sleep from screen light tonight."),
                tip='Get 10 to 15 minutes of outdoor light in the late afternoon, before the sun sets.',
                once_per_day=True, speakers=('light',)))

    # Evening: dim the lights after sunset / in the hour before bed.
    evening_start = min(sunset, bed - float(cfg.get('evening_dim_before_bed_min', 60)) * MIN)
    bright_since = s.get('bright_since')
    if now >= evening_start and bright_since and now - bright_since >= 5 * MIN and _is_bright(s, cfg):
        to_bed = bed - now
        when = (f'Bed is in {_mins(to_bed)} minutes' if 0 < to_bed <= 2 * 3600 else 'The sun has set')
        out.append(Nudge(
            'light', 'light.evening',
            evidence=f'{when} and the room is bright ({_light_words(s)}) for over 5 minutes.',
            text=(f"{when} and it's really bright in here. Dim the lights or switch to a small lamp; "
                  f"bright light at night pushes your sleep later."),
            tip='After sunset, and especially in the last hour before bed, use only as much light as you need; dim, warm lamps are best.',
            repeat_after_sec=float(cfg.get('repeat_after_min', 60)) * MIN, speakers=('light',)))

    # Daytime: dim room.
    dim_since = s.get('dim_since')
    day_dim = float(cfg.get('day_dim_min', 10)) * MIN
    if wake + 60 * MIN <= now <= sunset - 60 * MIN and dim_since and now - dim_since >= day_dim and _is_dim(s, cfg):
        out.append(Nudge(
            'light', 'light.daytime_dim',
            evidence=f'It is daytime and the room has been dim ({_light_words(s)}) for {_mins(now - dim_since)} minutes.',
            text=("It's daytime but it's pretty dim in here. Open the blinds or turn on a bright light; "
                  "bright days help you stay alert now and sleep better tonight."),
            tip='Bright light during the day (ideally outdoor light) supports alertness and a healthy sleep-wake rhythm.',
            repeat_after_sec=float(cfg.get('repeat_after_min', 60)) * MIN, speakers=('light',)))
    return out


def screen_nudges(s: dict, cfg: dict, now: float) -> list[Nudge]:
    """Sleep Protocol 14: no screens within an hour of bedtime."""
    if not cfg.get('enabled', True) or not s.get('at_desk') or not s.get('screen_on_desk'):
        return []
    bed = s['bed_ts']
    before = float(cfg.get('screens_off_before_bed_min', 60)) * MIN
    if bed - before <= now < bed + 3 * 3600:
        left = bed - now
        lead = f'Bed is at {clock_text(bed)}, {_mins(left)} minutes from now' if left > 0 else 'It is past your bedtime'
        return [Nudge(
            'screens', 'screens.bedtime',
            evidence=f'{lead}, and the computer is still on the desk with you at it.',
            text=(f"{lead}. The book says no screens in the last hour before sleep. "
                  f"If you really need me, turn my brightness way down."),
            tip='Avoid screens within an hour of bedtime; if you must use one, dim it and use the red / night mode.',
            once_per_day=True, speakers=('screens',))]
    return []


# ════════════════════════════════════════════════════════════════════
#  4. Phone-free time (Learning Protocol 1, Sleep Protocol 14)
# ════════════════════════════════════════════════════════════════════
def phone_nudges(s: dict, cfg: dict, now: float) -> list[Nudge]:
    if not cfg.get('enabled', True) or not s.get('at_desk') or not s.get('phone_counts', True):
        return []
    since = s.get('phone_on_desk_since')
    if not since:
        return []
    on_for = now - since
    bed = s['bed_ts']
    if bed - float(cfg.get('before_bed_min', 60)) * MIN <= now < bed + 3 * 3600 and on_for >= 2 * MIN:
        return [Nudge(
            'phone_free', 'phone.bedtime',
            evidence=f'Bed is at {clock_text(bed)} and the phone has been on the desk for {_mins(on_for)} minutes.',
            text=(f"Psst, it's the last hour before bed ({clock_text(bed)}). How about I go charge in another room? "
                  f"Screens this late make it harder to fall asleep."),
            tip='Avoid phones and other screens within an hour of bedtime.',
            once_per_day=True, speakers=('phone_free',))]
    if on_for >= float(cfg.get('nudge_after_min', 5)) * MIN:
        m = _mins(on_for)
        return [Nudge(
            'phone_free', 'phone.focus',
            evidence=f'The phone has been on the desk for {m} minutes while the user is working.',
            text=(f"I've been sitting next to you for {m} minutes. Even a phone in sight pulls at your focus. "
                  f"Put me in another room for a while?"),
            tip=('Put your phone away while you focus. Even having it in the same room, in a bag, '
                 'reduces your ability to focus.'),
            repeat_after_sec=float(cfg.get('repeat_after_min', 25)) * MIN, speakers=('phone_free', 'tasks'))]
    return []


# ════════════════════════════════════════════════════════════════════
#  Kept from the original Desk Talk reminders
# ════════════════════════════════════════════════════════════════════
def food_nudges(s: dict, cfg: dict, now: float) -> list[Nudge]:
    since = s.get('food_since')
    if not cfg.get('enabled', True) or not since:
        return []
    if now - since >= float(cfg.get('remind_after_min', 20)) * MIN:
        name = s.get('food_name') or 'snack'
        m = _mins(now - since)
        return [Nudge('food', f'food.sitting.{int(since)}',
                      evidence=f'A {name} has been on the desk for {m} minutes.',
                      text=f"This {name} has been next to you for {m} minutes. Eat it or move it away from the keyboard?",
                      tip='Keep food away from the keyboard and papers.',
                      repeat_after_sec=6 * 3600)]
    return []


def task_nudges(s: dict, cfg: dict, now: float) -> list[Nudge]:
    todos = [t for t in (s.get('open_todos') or []) if t]
    start = s.get('session_start')
    if not cfg.get('enabled', True) or not todos or not start or not s.get('at_desk'):
        return []
    every = float(cfg.get('remind_after_min', 40)) * MIN
    if now - start < every:
        return []
    n = len(todos)
    plural = 's' if n != 1 else ''
    first = todos[0][:60]
    return [Nudge('tasks', 'tasks.open',
                  evidence=f'The user has {n} open task{plural} on their list, starting with "{first}".',
                  text=f'You still have {n} open task{plural}, like "{first}". Want to pick one for the next 30 minutes?',
                  tip='Pick one task and focus on it for a set block of time.',
                  repeat_after_sec=every)]


RULES = (
    ('hydration', hydration_nudges),
    ('breaks', break_nudges),
    ('light', light_nudges),
    ('light', screen_nudges),
    ('phone_free', phone_nudges),
    ('food', food_nudges),
    ('tasks', task_nudges),
)


def candidate_nudges(state: dict, protocols_cfg: dict, now: float | None = None) -> list[Nudge]:
    """Every nudge whose reason is true right now (before cooldowns)."""
    now = now if now is not None else time.time()
    out = []
    for section, fn in RULES:
        out.extend(fn(state, protocols_cfg.get(section) or {}, now))
    return out


def pick_nudge(nudges: list[Nudge], last_fire: dict, now: float) -> Nudge | None:
    """The nudge to send now: not said too recently, least recently said habit first.
    `last_fire` maps rule key → when it was last sent (and 'habit:<name>' → same for the habit)."""
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
    if not ready:
        return None
    return min(ready, key=lambda n: last_fire.get(f'habit:{n.habit}', 0) or 0)
