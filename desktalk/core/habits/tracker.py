"""
The habit tracker: today's numbers for the Protocols habits.

    habit_days.py    HabitDay (one day's numbers) and HabitDays: loading, saving, a new day at midnight
    habit_reader.py  HabitReader: sips, breaks, phone-free time, light and food from the camera's DetectionEvents
    tracker.py       HabitTracker, the front: the buttons and chat actions (log), the counters other parts bump,
                     the nudges, and today() for the Habits page

When something happens (a sip, a break ...) it publishes a HabitEvent, which the chat turns
into a message. The day is saved in the store under "habits/<day>" (data/habits/<day>.json,
the same files as before), so a restart doesn't lose today's numbers.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, TypedDict

from desktalk.config import AgentsSettings, HabitTrackerSettings, PresenceSettings, Protocols
from desktalk.config.prompts import HabitNudges
from desktalk.core.habits import habit_rules as P
from desktalk.core.habits.habit_days import HabitDays, Said
from desktalk.core.habits.habit_reader import HabitReader, log_sip
from desktalk.core.habits.sun_times import sun_times
from desktalk.domain import DetectionEvent, Event, HabitEvent, Nudge, Todo
from desktalk.ports import Clock, DeskScene, EventBus, KeyValueStore, Preferences, Profiles

MIN = 60
HOUR = 3600


class DayTimes(TypedDict):
    wake_ts: float
    bed_ts: float
    sunset_ts: float | None
    sunrise_ts: float | None


class HabitTracker:
    """Implements HabitsView, HabitLog and HabitsLifecycle (desktalk/ports.py). Also day_times() for the web routes.
    `words` is config.prompts.habits."""

    def __init__(self, clock: Clock, bus: EventBus, store: KeyValueStore, profiles: Profiles,
                 prefs: Preferences, desk: DeskScene, protocols: Protocols, settings: HabitTrackerSettings,
                 presence: PresenceSettings, agents: AgentsSettings, *, phone_counts: bool, words: HabitNudges):
        self._clock = clock
        self._bus = bus
        self._prefs = prefs
        self._desk = desk
        self._rules = protocols
        self._s = settings
        self._words = words
        self._phone_counts = phone_counts                         # off when the phone itself is the camera
        self._lock = threading.RLock()     # the camera's events, the web routes and the background jobs all come here
        self._days = HabitDays(store, clock)
        self._reader = HabitReader(clock, profiles, desk, protocols, settings, presence, agents, words,
                                   phone_counts=phone_counts)
        self._last_fire: dict[str, float] = {}          # nudge key / 'habit:<name>' → when it was sent
        self._task_turn = 0                     # to-do check-ins sent: the next one names the to-do after
        bus.subscribe('habits', self._on_event, types=[DetectionEvent])

    # ── storage ──
    def save(self, force: bool = False) -> None:
        """Save today's numbers if they changed (or always, with force), and any earlier day not saved yet."""
        with self._lock:
            self._days.save(force)

    def _roll_day(self, now: float) -> None:
        if self._days.roll(now):
            self._reader.new_day()

    # ── helpers ──
    def day_times(self, now: float) -> DayTimes:
        """Wake time, bedtime, sunset and sunrise (if known) for the waking day containing `now`."""
        wake, bed = P.day_frame(now, self._prefs.get('wake_time'), self._prefs.get('bed_time'),
                                utc_offset_at=self._clock.utc_offset_sec)
        sched = self._rules.schedule
        sun = sun_times(now, sched.latitude, sched.longitude, sched.sunset_fallback, self._clock)
        return DayTimes(wake_ts=wake, bed_ts=bed, sunset_ts=sun['sunset'], sunrise_ts=sun['sunrise'])

    def _publish(self, said: Said, now: float, quiet: bool = False) -> None:
        habit, action, text, extra = said
        self._bus.publish(HabitEvent(ts=now, habit=habit, action=action, say=text, quiet=quiet, extra=extra))

    # ── the consumer ──
    def _on_event(self, event: Event) -> None:
        if isinstance(event, DetectionEvent):
            self.on_detection(event)

    def on_detection(self, event: DetectionEvent) -> None:
        now = event.ts
        with self._lock:
            self._roll_day(now)
            said = self._reader.read(event, self._days.today)
            self._days.changed()
            due_save = self._days.save_due(now, self._s.save_every_sec)
        for item in said:
            self._publish(item, now)
        if due_save:
            self.save()

    # ── things logged with the buttons or the chat ──
    def log(self, habit: str, action: str, **kw: Any) -> dict[str, Any]:
        """Buttons in the app and action tags in chat: hydration sip / glass, breaks break, light sunlight,
        food food / remove. Returns today(). Raises ValueError for an unknown habit / action or a bad number."""
        now = self._clock.now()
        item: Said | None
        logged = self._words.logged
        with self._lock:
            self._roll_day(now)
            day = self._days.today
            how = kw.get('how', 'button')
            if habit == 'hydration' and action == 'sip':
                item = log_sip(day, now, float(kw.get('ml') or self._rules.hydration.ml_per_sip), how, self._s,
                               self._words)
            elif habit == 'hydration' and action == 'glass':
                item = log_sip(day, now, float(kw.get('ml') or self._s.glass_ml), how, self._s, self._words)
            elif habit == 'breaks' and action == 'break':
                mins = max(1, int(float(kw.get('minutes') or self._s.button_break_min) * MIN // MIN))
                day.add_break(now, mins, 'button', self._s.breaks_kept)
                item = ('breaks', 'break', logged.break_button, {'minutes': mins, 'how': 'button', 'count': day.breaks})
                self._reader.restart_sitting(now)
            elif habit == 'light' and action == 'sunlight':
                mins = int(kw.get('minutes') or self._s.sunlight_default_min)
                sunset = self.day_times(now)['sunset_ts']
                afternoon = sunset is not None and now >= sunset - self._s.afternoon_light_hours * HOUR
                if afternoon:
                    day.afternoon_light_ts = now
                else:
                    day.sunlight_ts = now
                day.sunlight_minutes += mins
                when = logged.sunlight_afternoon if afternoon else logged.sunlight_morning
                item = ('light', 'sunlight', logged.sunlight.format(minutes=mins, when=when),
                        {'minutes': mins, 'afternoon': afternoon})
            elif habit == 'food' and action == 'food':
                label = str(kw.get('label') or self._words.unknown_snack)[:self._s.food_label_max_chars]
                healthy = bool(kw.get('healthy'))
                day.add_food(label, healthy, now, 'button')
                item = ('food', 'logged', logged.food_button.format(food=label), {'label': label, 'healthy': healthy})
            elif habit == 'food' and action == 'remove':
                i = int(kw.get('index', -1))
                if 0 <= i < len(day.food):
                    day.food.pop(i)
                    self._days.changed()
                item = None
            else:
                raise ValueError(f'unknown habit log: {habit}/{action}')
            if item:
                self._days.changed()
        if item:
            self._publish(item, now, quiet=bool(kw.get('quiet')))
        self.save()
        return self.today(now)

    def bump(self, key: str, amount: int = 1) -> None:
        """Daily counters other parts update: danger_count, lost_alerts. ValueError for another name."""
        with self._lock:
            self._roll_day(self._clock.now())
            self._days.today.bump(key, amount)
            self._days.changed()

    def note_fired(self, nudge: Nudge, now: float) -> None:
        """A nudge went out: it (and its habit) waits before the next one."""
        with self._lock:
            self._last_fire[nudge.key] = now
            self._last_fire[f'habit:{nudge.habit}'] = now
            if nudge.key == P.TASK_CHECK_IN:
                self._task_turn += 1
            self._days.today.bump('nudges_sent')
            self._days.changed()

    # ── reading ──
    def rule_state(self, now: float, open_todos: Sequence[Todo] = ()) -> P.RuleState:
        """The snapshot the Protocols rules look at. Due dates go by the clock's local day."""
        t = self.day_times(now)
        session_start = self._desk.session_started_at()
        with self._lock:
            r, d = self._reader, self._days.today
            return P.RuleState(
                at_desk=r.at_desk(now), session_start=session_start,
                wake_ts=t['wake_ts'], bed_ts=t['bed_ts'], sunset_ts=t['sunset_ts'],
                water_ml=d.water_ml, last_sip_ts=d.last_sip_ts,
                drink_on_desk=r.drink_on_desk,
                sit_since=r.sit_since,
                meal_done_ts=r.meal_done_ts, meal_name=r.meal_name,
                food_since=r.food_since, food_name=r.food_name,
                phone_on_desk_since=r.phone_on_desk_since if self._phone_counts else None,
                phone_counts=self._phone_counts,
                sunlight_ts=d.sunlight_ts, afternoon_light_ts=d.afternoon_light_ts,
                lux=r.light.get('lux'), brightness=r.light.get('brightness'),
                bright_since=r.bright_since, dim_since=r.dim_since,
                screen_on_desk=r.screen_on_desk,
                open_todos=tuple(open_todos), today=self._clock.local_day(now), task_turn=self._task_turn,
                utc_offset_sec=self._clock.utc_offset_sec(now),
                utc_offset_at=self._clock.utc_offset_sec,
            )

    def nudges(self, now: float, open_todos: Sequence[Todo]) -> list[Nudge]:
        """The nudges that may go out now, best first: their reason is true and they weren't sent too recently.
        The least recently heard habit comes first."""
        candidates = P.candidate_nudges(self.rule_state(now, open_todos), self._rules, now, self._words)
        with self._lock:
            last_fire = dict(self._last_fire)
        return P.ready_nudges(candidates, last_fire, now, day_of=self._clock.local_day)

    def today(self, now: float | None = None) -> dict[str, Any]:
        """Everything the Habits page and the trackers show."""
        now = self._clock.now() if now is None else now
        t = self.day_times(now)
        hyd = self._rules.hydration
        window = hyd.goal_window_hours * HOUR
        goal = hyd.daily_goal_ml
        with self._lock:
            r = self._reader
            d = self._days.today.to_dict()
            sit = round(now - r.sit_since) if (r.sit_since and r.at_desk(now)) else 0
            streak = round(r.phone_free_streak)
            phone_now = r.phone_on_desk() if self._phone_counts else None
            light = dict(r.light)
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
