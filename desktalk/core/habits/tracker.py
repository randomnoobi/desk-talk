"""
The habit tracker: today's numbers for the Protocols habits.

It listens to DetectionEvents and keeps count of:
  hydration   sips (the cup leaves the desk and comes back) → estimated water
  breaks      away from the desk 5+ minutes; how long you've been sitting
  phone-free  desk time with no phone on the desk, and the current streak
  light       how bright the room is, and the sunlight you logged
  food        what was on the desk (from the original snack log)

When something happens (a sip, a break ...) it publishes a HabitEvent, which the chat turns
into a message. The day is saved in the store under "habits/<day>" (data/habits/<day>.json,
the same files as before), so a restart doesn't lose today's numbers.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Sequence
from typing import Any

from desktalk.config import AgentsSettings, HabitTrackerSettings, PresenceSettings, Protocols
from desktalk.core.agents.presence import PresenceTracker
from desktalk.core.habits import protocols as P
from desktalk.core.habits.sun_times import sun_times
from desktalk.desk_state import DeskState
from desktalk.domain import DetectionEvent, HabitEvent, Nudge
from desktalk.ports import Clock, EventBus, KeyValueStore, Preferences, Profiles
from desktalk.util import a_or_an, local_day

log = logging.getLogger(__name__)

MIN = 60
HOUR = 3600
HOURS_IN_DAY = 24
HEALTHY_FOODS = frozenset({
    'banana', 'apple', 'orange', 'broccoli', 'carrot', 'pear', 'peach', 'strawberry', 'grapes', 'tangerine', 'nuts',
    'kiwi fruit', 'cucumber', 'tomato', 'avocado', 'mango', 'cherry', 'plum', 'lettuce', 'green vegetables'})


def day_key(day: str) -> str:
    """The store key for one day's numbers: habits/2026-10-21."""
    return f'habits/{day}'


def new_day(day: str) -> dict[str, Any]:
    """A fresh day, laid out exactly as the day files have always been."""
    return {
        'day': day,
        'sips': 0, 'water_ml': 0, 'last_sip_ts': None, 'drinks': [],
        'breaks': 0, 'last_break_ts': None, 'break_log': [], 'longest_sit_sec': 0,
        'desk_sec': 0.0, 'phone_free_sec': 0.0, 'phone_on_desk_sec': 0.0, 'best_phone_free_sec': 0.0,
        'food': [], 'sunlight_ts': None, 'sunlight_minutes': 0, 'afternoon_light_ts': None,
        'hourly_activity': [0] * HOURS_IN_DAY, 'objects_seen': [],
        'danger_count': 0, 'lost_alerts': 0, 'nudges_sent': 0, 'summary_sent': False,
    }


# What happened, for the chat: (habit, action, words, extra fields for the HabitEvent)
Said = tuple[str, str, str, dict[str, Any]]


class HabitTracker:
    """Implements HabitsView (desktalk/ports.py). Also log() and day_times() for the web routes."""

    def __init__(self, clock: Clock, bus: EventBus, store: KeyValueStore, profiles: Profiles,
                 prefs: Preferences, desk: DeskState, protocols: Protocols, settings: HabitTrackerSettings,
                 presence: PresenceSettings, agents: AgentsSettings, *, phone_counts: bool):
        self._clock = clock
        self._bus = bus
        self._store = store
        self._profiles = profiles
        self._prefs = prefs
        self._desk = desk
        self._rules = protocols
        self._s = settings
        self._min_conf = agents.min_confidence
        self._user_window = presence.user_active_window_sec      # shorter than reminders.someone_here_sec
        self._phone_counts = phone_counts                         # off when the phone itself is the camera
        self._cats = {
            'drink': profiles.labels_for_habit('hydration'),
            'food': profiles.labels_for_habit('food'),
            'phone': profiles.labels_for_habit('phone_free'),
            'screen': profiles.labels_for_habit('screens'),
        }
        self._presence = PresenceTracker(presence.appear_after_sec, presence.disappear_after_sec)
        self._lock = threading.RLock()     # re-entrant: a new day saves while holding it
        self._d = self._load(local_day(clock.now()))
        self._dirty = False
        self._last_save = 0.0
        # moment-to-moment state (not saved)
        self._last_event_ts: float | None = None
        self._user_last_seen: float | None = None
        self._away_since: float | None = None
        self._sit_since: float | None = None
        self._drink_gone_since: dict[str, float] = {}   # "drink:cup" → when it was last seen before leaving
        self._food_since: float | None = None
        self._food_name = ''
        self._meal_done_ts: float | None = None
        self._meal_name = ''
        self._phone_on_desk_since: float | None = None
        self._phone_free_streak = 0.0
        self._celebrated = 0
        self._bright_since: float | None = None
        self._dim_since: float | None = None
        self._light: dict[str, float | None] = {}
        self._screen_on_desk = False
        self._drink_on_desk = False
        self._last_fire: dict[str, float] = {}          # nudge key / 'habit:<name>' → when it was sent
        bus.subscribe('habits', self.on_detection, types=[DetectionEvent])

    # ── storage ──
    def _load(self, day: str) -> dict[str, Any]:
        d = new_day(day)
        saved = self._store.load(day_key(day))
        if isinstance(saved, dict) and saved.get('day') == day:
            d.update({k: v for k, v in saved.items() if k in d})
        elif saved is not None:
            log.warning('The habit file for %s is not for that day or is broken; starting the day fresh', day)
        return d

    def save(self, force: bool = False) -> None:
        """Save today's numbers if they changed (or always, with force)."""
        with self._lock:
            if not (self._dirty or force):
                return
            data = copy.deepcopy(self._d)
            self._dirty = False
            self._last_save = self._clock.now()
            try:
                self._store.save(day_key(data['day']), data)
            except OSError as exc:
                self._dirty = True              # try again next time
                log.warning('Could not save the habits for %s: %s', data['day'], exc)

    def _roll_day(self, now: float) -> None:
        day = local_day(now)
        if day != self._d['day']:
            self.save(force=True)
            self._d = self._load(day)
            self._celebrated = 0
            self._phone_free_streak = 0.0

    # ── helpers ──
    def day_times(self, now: float) -> dict[str, float | None]:
        """Wake time, bedtime, sunset and sunrise (if known) for the waking day containing `now`."""
        wake, bed = P.day_frame(now, self._prefs.get('wake_time'), self._prefs.get('bed_time'))
        sched = self._rules.schedule
        sun = sun_times(now, sched.latitude, sched.longitude, sched.sunset_fallback)
        return {'wake_ts': wake, 'bed_ts': bed, 'sunset_ts': sun['sunset'], 'sunrise_ts': sun['sunrise']}

    def _publish(self, said: Said, now: float, quiet: bool = False) -> None:
        habit, action, text, extra = said
        self._bus.publish(HabitEvent(ts=now, habit=habit, action=action, say=text, quiet=quiet, extra=extra))

    def _best_per_label(self, event: DetectionEvent) -> dict[str, float]:
        """Detector word → its best confidence, for the words sure enough to count."""
        best: dict[str, float] = {}
        for det in event.detections:
            prof = self._profiles.for_label(det.label)
            need = prof.min_confidence if (prof and prof.min_confidence is not None) else self._min_conf
            if det.confidence >= need and det.confidence > best.get(det.label, -1):
                best[det.label] = det.confidence
        return best

    def _at_desk(self, now: float) -> bool:
        return (self._away_since is None and self._user_last_seen is not None
                and now - self._user_last_seen <= self._user_window)

    # ── the consumer ──
    def on_detection(self, event: DetectionEvent) -> None:
        now = event.ts
        said: list[Said] = []
        with self._lock:
            self._roll_day(now)
            break_sec = self._rules.breaks.break_min * MIN
            gap = (now - self._last_event_ts) if self._last_event_ts else 0.0
            if self._last_event_ts and gap >= break_sec:
                # The camera was off this long: that time away counts as a break.
                said.append(self._log_break(now, gap, 'camera off'))
                self._sit_since = now
                self._away_since = None
            dt = min(gap, self._s.gap_counted_max_sec)
            self._last_event_ts = now

            # Someone at the desk? (a person, or something moving in the picture)
            if event.person or event.moved:
                if self._away_since is not None and now - self._away_since >= break_sec:
                    said.append(self._log_break(now, now - self._away_since, 'left the desk'))
                    self._sit_since = now
                elif self._sit_since is None:
                    self._sit_since = now
                self._away_since = None
                self._user_last_seen = now
            elif (self._user_last_seen is not None and self._away_since is None
                  and now - self._user_last_seen > self._user_window):
                self._away_since = self._user_last_seen
            at_desk = self._at_desk(now)
            if at_desk:
                self._d['desk_sec'] += dt
                self._d['hourly_activity'][time.localtime(now).tm_hour] += dt
                if self._sit_since:
                    self._d['longest_sit_sec'] = max(self._d['longest_sit_sec'], round(now - self._sit_since))

            # Things on the desk, by habit. Drinks are also tracked one kind at a time
            # ("drink:cup", "drink:bottle"), so picking up the mug counts even if a bottle stays.
            labels = self._best_per_label(event)
            seen = {cat for cat, words in self._cats.items() if words & set(labels)}
            seen |= {f'drink:{label}' for label in labels if label in self._cats['drink']}
            appeared, gone = self._presence.update(seen, now)
            self._drink_on_desk = self._presence.is_present('drink')
            self._screen_on_desk = self._presence.is_present('screen')
            said += self._sips(appeared, gone, now)
            said += self._food(labels, appeared, gone, now)
            if self._phone_counts:
                said += self._phone(appeared, gone, at_desk, dt, now)

            # Light
            lux, brightness = event.light.lux, event.light.brightness
            self._light = {'lux': lux, 'brightness': brightness}
            light_rule = self._rules.light
            self._bright_since = (self._bright_since or now) if P.is_bright(lux, brightness, light_rule) else None
            self._dim_since = (self._dim_since or now) if P.is_dim(lux, brightness, light_rule) else None

            with self._desk.lock:
                names = [p.name for p in (self._profiles.get(pid) for pid in self._desk.objects) if p]
            for name in names:
                if name not in self._d['objects_seen']:
                    self._d['objects_seen'].append(name)
            self._dirty = True
            due_save = now - self._last_save >= self._s.save_every_sec
        for item in said:
            self._publish(item, now)
        if due_save:
            self.save()

    def _sips(self, appeared: list, gone: list, now: float) -> list[Said]:
        hyd = self._rules.hydration
        for key in gone:
            if key.startswith('drink:'):
                self._drink_gone_since[key] = self._presence.last_seen(key) or now
        out = []
        for key in appeared:
            since = self._drink_gone_since.pop(key, None) if key.startswith('drink:') else None
            if since is not None and hyd.sip_gone_min_sec <= now - since <= hyd.sip_gone_max_sec:
                out.append(self._log_sip(now, hyd.ml_per_sip, 'camera'))
        return out

    def _food(self, labels: dict[str, float], appeared: list, gone: list, now: float) -> list[Said]:
        out = []
        if 'food' in appeared:
            foods = [(label, conf) for label, conf in labels.items() if label in self._cats['food']]
            name = max(foods, key=lambda x: x[1], default=('snack', 0))[0]
            self._food_since, self._food_name = now, name
            if not any(f.get('label') == name for f in self._d['food']):
                healthy = name in HEALTHY_FOODS
                self._d['food'].append({'label': name, 'healthy': healthy, 'ts': now, 'how': 'camera'})
                emoji = '\U0001F34E' if healthy else '\U0001F355'
                out.append(('food', 'logged', f'{emoji} Spotted {a_or_an(name)} {name} on the desk, so I logged it!',
                            {'label': name, 'healthy': healthy}))
        if 'food' in gone and self._food_since:
            stayed = (self._presence.last_seen('food') or now) - self._food_since
            if stayed >= self._rules.breaks.meal_min_on_desk_min * MIN:
                self._meal_done_ts, self._meal_name = now, self._food_name
            self._food_since = None
        return out

    def _phone(self, appeared: list, gone: list, at_desk: bool, dt: float, now: float) -> list[Said]:
        if 'phone' in appeared:
            self._phone_on_desk_since = now
            self._phone_free_streak = 0.0
            self._celebrated = 0
        if 'phone' in gone:
            self._phone_on_desk_since = None
        if not at_desk:
            return []
        if self._presence.is_present('phone'):
            self._d['phone_on_desk_sec'] += dt
            return []
        self._d['phone_free_sec'] += dt
        self._phone_free_streak += dt
        self._d['best_phone_free_sec'] = max(self._d['best_phone_free_sec'], round(self._phone_free_streak))
        every = self._rules.phone_free.celebrate_every_min * MIN
        if every > 0 and self._phone_free_streak >= every * (self._celebrated + 1):
            self._celebrated += 1
            mins = int(self._phone_free_streak // MIN)
            return [('phone_free', 'streak',
                     f'{mins} minutes at the desk with no phone in sight. That is real focus time!',
                     {'minutes': mins})]
        return []

    # ── things that happened (camera or buttons) ──
    def _log_break(self, now: float, seconds: float, how: str) -> Said:
        mins = max(1, int(seconds // MIN))
        self._d['breaks'] += 1
        self._d['last_break_ts'] = now
        self._d['break_log'].append({'ts': now, 'minutes': mins, 'how': how})
        self._d['break_log'] = self._d['break_log'][-self._s.breaks_kept:]
        self._dirty = True
        text = (f'Nice, you were away for {mins} min. Break logged automatically!' if how != 'button'
                else 'Break logged. Roll your shoulders while you are at it.')
        return 'breaks', 'break', text, {'minutes': mins, 'how': how, 'count': self._d['breaks']}

    def _log_sip(self, now: float, ml: float, how: str) -> Said:
        self._d['sips'] += 1
        self._d['water_ml'] = int(self._d['water_ml'] + ml)
        self._d['last_sip_ts'] = now
        self._d['drinks'].append({'ts': now, 'ml': int(ml), 'how': how})
        self._d['drinks'] = self._d['drinks'][-self._s.drinks_kept:]
        self._dirty = True
        goal = int(self._rules.hydration.daily_goal_ml)
        liters = self._d['water_ml'] / 1000
        if how == 'camera':
            text = (f"Sip detected! That's {self._d['sips']} today (about {liters:.1f} of {goal / 1000:.1f} L). "
                    f"Keep it up!")
        else:
            text = f'Logged! About {liters:.1f} of {goal / 1000:.1f} L today.'
        return 'hydration', 'sip', text, {'ml': int(ml), 'total_ml': self._d['water_ml'], 'how': how}

    def log(self, habit: str, action: str, **kw: Any) -> dict[str, Any]:
        """Buttons in the app and action tags in chat: hydration sip / glass, breaks break, light sunlight,
        food food / remove. Returns today(). Raises ValueError for an unknown habit / action or a bad number."""
        now = self._clock.now()
        item: Said | None
        with self._lock:
            self._roll_day(now)
            how = kw.get('how', 'button')
            if habit == 'hydration' and action == 'sip':
                item = self._log_sip(now, float(kw.get('ml') or self._rules.hydration.ml_per_sip), how)
            elif habit == 'hydration' and action == 'glass':
                item = self._log_sip(now, float(kw.get('ml') or self._s.glass_ml), how)
            elif habit == 'breaks' and action == 'break':
                item = self._log_break(now, float(kw.get('minutes') or self._s.button_break_min) * MIN, 'button')
                self._sit_since = now
            elif habit == 'light' and action == 'sunlight':
                mins = int(kw.get('minutes') or self._s.sunlight_default_min)
                afternoon = now >= self.day_times(now)['sunset_ts'] - self._s.afternoon_light_hours * HOUR
                self._d['afternoon_light_ts' if afternoon else 'sunlight_ts'] = now
                self._d['sunlight_minutes'] += mins
                self._dirty = True
                item = ('light', 'sunlight', f'Logged {mins} minutes of {"late-afternoon" if afternoon else "morning"} '
                        f'sunlight. Your body clock says thanks!', {'minutes': mins, 'afternoon': afternoon})
            elif habit == 'food' and action == 'food':
                label = str(kw.get('label') or 'snack')[:self._s.food_label_max_chars]
                healthy = bool(kw.get('healthy'))
                self._d['food'].append({'label': label, 'healthy': healthy, 'ts': now, 'how': 'button'})
                self._dirty = True
                item = ('food', 'logged', f'Logged {label}.', {'label': label, 'healthy': healthy})
            elif habit == 'food' and action == 'remove':
                i = int(kw.get('index', -1))
                if 0 <= i < len(self._d['food']):
                    self._d['food'].pop(i)
                    self._dirty = True
                item = None
            else:
                raise ValueError(f'unknown habit log: {habit}/{action}')
        if item:
            self._publish(item, now, quiet=bool(kw.get('quiet')))
        self.save()
        return self.today(now)

    def bump(self, key: str, amount: int = 1) -> None:
        """Daily counters other parts update (danger_count, lost_alerts ...)."""
        with self._lock:
            self._roll_day(self._clock.now())
            self._d[key] = self._d.get(key, 0) + amount
            self._dirty = True

    def note_fired(self, nudge: Nudge, now: float) -> None:
        """A nudge went out: it (and its habit) waits before the next one."""
        with self._lock:
            self._last_fire[nudge.key] = now
            self._last_fire[f'habit:{nudge.habit}'] = now
            self._d['nudges_sent'] += 1
            self._dirty = True

    # ── reading ──
    def rule_state(self, now: float, open_todos: Sequence[str] = ()) -> P.RuleState:
        """The snapshot the Protocols rules look at."""
        t = self.day_times(now)
        with self._lock:
            with self._desk.lock:
                session_start = self._desk.session_start
            return P.RuleState(
                at_desk=self._at_desk(now), session_start=session_start,
                wake_ts=t['wake_ts'], bed_ts=t['bed_ts'], sunset_ts=t['sunset_ts'],
                water_ml=self._d['water_ml'], sips=self._d['sips'], last_sip_ts=self._d['last_sip_ts'],
                drink_on_desk=self._drink_on_desk,
                sit_since=self._sit_since, last_break_ts=self._d['last_break_ts'],
                meal_done_ts=self._meal_done_ts, meal_name=self._meal_name,
                food_since=self._food_since, food_name=self._food_name,
                phone_on_desk_since=self._phone_on_desk_since if self._phone_counts else None,
                phone_counts=self._phone_counts,
                sunlight_ts=self._d['sunlight_ts'], afternoon_light_ts=self._d['afternoon_light_ts'],
                lux=self._light.get('lux'), brightness=self._light.get('brightness'),
                bright_since=self._bright_since, dim_since=self._dim_since,
                screen_on_desk=self._screen_on_desk,
                open_todos=tuple(open_todos),
            )

    def nudges(self, now: float, open_todos: Sequence[str]) -> list[Nudge]:
        """The nudges that may go out now, best first: their reason is true and they weren't sent too recently.
        The least recently heard habit comes first."""
        candidates = P.candidate_nudges(self.rule_state(now, open_todos), self._rules, now)
        with self._lock:
            last_fire = dict(self._last_fire)
        return P.ready_nudges(candidates, last_fire, now)

    def today(self, now: float | None = None) -> dict[str, Any]:
        """Everything the Habits page and the trackers show."""
        now = self._clock.now() if now is None else now
        t = self.day_times(now)
        hyd = self._rules.hydration
        window = hyd.goal_window_hours * HOUR
        goal = hyd.daily_goal_ml
        with self._lock:
            d = copy.deepcopy(self._d)
            sit = round(now - self._sit_since) if (self._sit_since and self._at_desk(now)) else 0
            streak = round(self._phone_free_streak)
            phone_now = self._presence.is_present('phone') if self._phone_counts else None
            light = dict(self._light)
        return {
            'day': d['day'], 'times': t,
            'hydration': {'sips': d['sips'], 'water_ml': d['water_ml'], 'goal_ml': int(goal),
                          'on_waking_ml': int(hyd.on_waking_ml),
                          'pace_ml': int(goal * min(1.0, max(0.0, (now - t['wake_ts']) / window))),
                          'last_sip_ts': d['last_sip_ts'], 'ml_per_sip': int(hyd.ml_per_sip)},
            'breaks': {'count': d['breaks'], 'last_break_ts': d['last_break_ts'], 'sitting_sec': sit,
                       'longest_sit_sec': d['longest_sit_sec'],
                       'sit_limit_min': int(self._rules.breaks.sit_limit_min),
                       'log': d['break_log'][-self._s.breaks_shown:] if self._s.breaks_shown else []},
            'phone_free': {'counted': self._phone_counts, 'phone_free_sec': round(d['phone_free_sec']),
                           'phone_on_desk_sec': round(d['phone_on_desk_sec']), 'streak_sec': streak,
                           'best_sec': d['best_phone_free_sec'], 'phone_on_desk': phone_now,
                           'goal_min': int(self._rules.phone_free.goal_min_per_day)},
            'light': {'sunlight_ts': d['sunlight_ts'], 'afternoon_light_ts': d['afternoon_light_ts'],
                      'sunlight_minutes': d['sunlight_minutes'], 'lux': light.get('lux'),
                      'brightness': light.get('brightness')},
            'food': {'entries': d['food']},
            'desk_sec': round(d['desk_sec']),
            'danger_count': d['danger_count'], 'lost_alerts': d['lost_alerts'],
            'hourly_activity': [round(x) for x in d['hourly_activity']],
            'objects_seen': d['objects_seen'],
        }
