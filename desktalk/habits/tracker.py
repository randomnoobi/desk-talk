"""
The habit tracker: today's numbers for the four Protocols habits.

It listens to detection events and keeps count of:
  hydration   sips (the cup leaves the desk and comes back) → estimated water
  breaks      away from the desk 5+ minutes; how long you've been sitting
  phone-free  desk time with no phone on the desk, and the current streak
  light       how bright the room is, and the sunlight you logged
  food        what was on the desk (from the original snack log)

When something happens (a sip, a break...) it publishes a "habit" event, which
the chat turns into a message. The day is saved in data/habits/<day>.json so a
restart doesn't lose today's numbers.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .. import paths
from ..agents.roster import PresenceTracker
from ..events.stream import HABIT
from ..util import local_day
from . import protocols as P
from .sun_times import sun_times

HEALTHY_FOODS = {'banana', 'apple', 'orange', 'broccoli', 'carrot', 'pear', 'peach', 'strawberry', 'grapes',
                 'tangerine', 'nuts', 'kiwi fruit', 'cucumber', 'tomato', 'avocado', 'mango', 'cherry', 'plum',
                 'lettuce', 'green vegetables'}


def new_day(day: str) -> dict:
    return {
        'day': day,
        'sips': 0, 'water_ml': 0, 'last_sip_ts': None, 'drinks': [],
        'breaks': 0, 'last_break_ts': None, 'break_log': [], 'longest_sit_sec': 0,
        'desk_sec': 0.0, 'phone_free_sec': 0.0, 'phone_on_desk_sec': 0.0, 'best_phone_free_sec': 0.0,
        'food': [], 'sunlight_ts': None, 'sunlight_minutes': 0, 'afternoon_light_ts': None,
        'hourly_activity': [0] * 24, 'objects_seen': [],
        'danger_count': 0, 'lost_alerts': 0, 'nudges_sent': 0, 'summary_sent': False,
    }


class HabitTracker:
    def __init__(self, settings, profiles, stream, desk, prefs, folder: Path | None = None,
                 phone_counts: bool = True, clock=time.time):
        self.cfg = settings.protocols
        self.profiles = profiles
        self.stream = stream
        self.desk = desk
        self.prefs = prefs
        self.folder = Path(folder) if folder else paths.DATA_DIR / 'habits'
        self.phone_counts = phone_counts           # off when the phone itself is the camera
        self.clock = clock
        self.min_conf = float(settings.agents.get('min_confidence', 0.45))
        pres = settings.get('presence') or {}
        self.user_window = float(pres.get('user_active_window_sec', 90))
        self.cats = {
            'drink': profiles.labels_for_habit('hydration'),
            'food': profiles.labels_for_habit('food'),
            'phone': profiles.labels_for_habit('phone_free'),
            'screen': profiles.labels_for_habit('screens'),
        }
        self.presence = PresenceTracker(pres.get('appear_after_sec', 1.0), pres.get('disappear_after_sec', 2.5))
        self._lock = threading.RLock()     # re-entrant: a new day saves while holding it
        self.d = self._load(local_day(clock()))
        self._dirty = False
        self._last_save = 0.0
        # moment-to-moment state (not saved)
        self.last_event_ts = None
        self.user_last_seen = None
        self.away_since = None
        self.sit_since = None
        self.drink_gone_since: dict = {}   # "drink:cup" → when it was last seen before leaving
        self.food_since = None
        self.food_name = ''
        self.meal_done_ts = None
        self.meal_name = ''
        self.phone_on_desk_since = None
        self.phone_free_streak = 0.0
        self._celebrated = 0
        self.bright_since = None
        self.dim_since = None
        self.light = {}
        self.screen_on_desk = False
        self.drink_on_desk = False
        self.last_fire: dict = {}          # nudge key / 'habit:<name>' → ts (for protocols.pick_nudge)

    # ── storage ──
    def _path(self, day: str) -> Path:
        return self.folder / f'{day}.json'

    def _load(self, day: str) -> dict:
        d = new_day(day)
        try:
            saved = json.loads(self._path(day).read_text(encoding='utf-8'))
            if isinstance(saved, dict) and saved.get('day') == day:
                d.update({k: v for k, v in saved.items() if k in d})
        except (OSError, ValueError):
            pass
        return d

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not (self._dirty or force):
                return
            data = json.loads(json.dumps(self.d))
            self._dirty = False
            self._last_save = self.clock()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            p = self._path(data['day'])
            tmp = p.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, indent=1), encoding='utf-8')
            os.replace(tmp, p)
        except OSError as exc:
            print(f'  [habits] could not save: {exc}')

    def _roll_day(self, now: float) -> None:
        day = local_day(now)
        if day != self.d['day']:
            self.save(force=True)
            self.d = self._load(day)
            self._celebrated = 0
            self.phone_free_streak = 0.0

    # ── helpers ──
    def day_times(self, now: float) -> dict:
        wake, bed = P.day_frame(now, self.prefs.get('wake_time'), self.prefs.get('bed_time'))
        sched = self.cfg.get('schedule') or {}
        sun = sun_times(now, sched.get('latitude'), sched.get('longitude'), sched.get('sunset_fallback', '18:30'))
        return {'wake_ts': wake, 'bed_ts': bed, 'sunset_ts': sun['sunset'], 'sunrise_ts': sun['sunrise']}

    def _say(self, habit: str, action: str, text: str, now: float, **extra) -> None:
        self.stream.publish(HABIT, ts=now, habit=habit, action=action, say=text, **extra)

    def _labels(self, ev: dict) -> dict:
        best = {}
        for o in ev.get('detected_obj') or []:
            lab = o.get('label')
            prof = self.profiles.for_label(lab) if lab else None
            need = prof.min_confidence if (prof and prof.min_confidence is not None) else self.min_conf
            if lab and o.get('conf', 0) >= need and (lab not in best or o['conf'] > best[lab]['conf']):
                best[lab] = o
        return best

    def at_desk(self, now: float) -> bool:
        return (self.away_since is None and self.user_last_seen is not None
                and now - self.user_last_seen <= self.user_window)

    # ── the consumer ──
    def on_detection(self, ev: dict) -> None:
        now = (float(ev['ts']) if ev.get('ts') is not None else self.clock())
        say = []
        with self._lock:
            self._roll_day(now)
            brk = self.cfg.get('breaks') or {}
            break_sec = float(brk.get('break_min', 5)) * 60
            gap = (now - self.last_event_ts) if self.last_event_ts else 0.0
            if self.last_event_ts and gap >= break_sec:
                # The camera was off this long: that time away counts as a break (as before).
                say.append(self._log_break_locked(now, gap, 'camera off'))
                self.sit_since = now
                self.away_since = None
            dt = min(gap, 2.0)
            self.last_event_ts = now

            # Someone at the desk? (a person, or something moving in the picture)
            if ev.get('person') or ev.get('moved'):
                if self.away_since is not None and now - self.away_since >= break_sec:
                    say.append(self._log_break_locked(now, now - self.away_since, 'left the desk'))
                    self.sit_since = now
                elif self.sit_since is None:
                    self.sit_since = now
                self.away_since = None
                self.user_last_seen = now
            elif (self.user_last_seen is not None and self.away_since is None
                  and now - self.user_last_seen > self.user_window):
                self.away_since = self.user_last_seen
            at_desk = self.at_desk(now)
            if at_desk:
                self.d['desk_sec'] += dt
                self.d['hourly_activity'][time.localtime(now).tm_hour] += dt
                if self.sit_since:
                    self.d['longest_sit_sec'] = max(self.d['longest_sit_sec'], round(now - self.sit_since))

            # Things on the desk, by habit category. Drinks are tracked one kind at a time
            # ("drink:cup", "drink:bottle"), so picking up the mug counts even if a bottle stays.
            labels = self._labels(ev)
            seen = {cat for cat, labs in self.cats.items() if labs and labs & set(labels)}
            seen |= {f'drink:{l}' for l in labels if l in self.cats['drink']}
            appeared, gone = self.presence.update(seen, now)
            self.drink_on_desk = self.presence.is_present('drink')
            self.screen_on_desk = self.presence.is_present('screen')

            hyd = self.cfg.get('hydration') or {}
            for key in gone:
                if key.startswith('drink:'):
                    self.drink_gone_since[key] = self.presence.last_seen.get(key, now)
            for key in appeared:
                since = self.drink_gone_since.pop(key, None) if key.startswith('drink:') else None
                if since is None:
                    continue
                away = now - since
                if float(hyd.get('sip_gone_min_sec', 3)) <= away <= float(hyd.get('sip_gone_max_sec', 120)):
                    say.append(self._log_sip_locked(now, float(hyd.get('ml_per_sip', 80)), 'camera'))

            if 'food' in appeared:
                name = max(((l, o['conf']) for l, o in labels.items() if l in self.cats['food']),
                           key=lambda x: x[1], default=('snack', 0))[0]
                self.food_since, self.food_name = now, name
                if not any(f.get('label') == name for f in self.d['food']):
                    healthy = name in HEALTHY_FOODS
                    self.d['food'].append({'label': name, 'healthy': healthy, 'ts': now, 'how': 'camera'})
                    emoji = '\U0001F34E' if healthy else '\U0001F355'
                    say.append(('food', 'logged', f'{emoji} Spotted {"an" if name[:1] in "aeiou" else "a"} {name} on the desk, so I logged it!',
                                {'label': name, 'healthy': healthy}))
            if 'food' in gone and self.food_since:
                stayed = self.presence.last_seen.get('food', now) - self.food_since
                if stayed >= float(brk.get('meal_min_on_desk_min', 3)) * 60:
                    self.meal_done_ts, self.meal_name = now, self.food_name
                self.food_since = None

            if self.phone_counts:
                if 'phone' in appeared:
                    self.phone_on_desk_since = now
                    self.phone_free_streak = 0.0
                    self._celebrated = 0
                if 'phone' in gone:
                    self.phone_on_desk_since = None
                if at_desk:
                    if self.presence.is_present('phone'):
                        self.d['phone_on_desk_sec'] += dt
                    else:
                        self.d['phone_free_sec'] += dt
                        self.phone_free_streak += dt
                        self.d['best_phone_free_sec'] = max(self.d['best_phone_free_sec'], round(self.phone_free_streak))
                        every = float((self.cfg.get('phone_free') or {}).get('celebrate_every_min', 45)) * 60
                        if every > 0 and self.phone_free_streak >= every * (self._celebrated + 1):
                            self._celebrated += 1
                            mins = int(self.phone_free_streak // 60)
                            say.append(('phone_free', 'streak',
                                        f'{mins} minutes at the desk with no phone in sight. That is real focus time!',
                                        {'minutes': mins}))

            # Light
            light = ev.get('light') or {}
            self.light = light
            lcfg = self.cfg.get('light') or {}
            st = {'lux': light.get('lux'), 'brightness': light.get('brightness')}
            self.bright_since = (self.bright_since or now) if P._is_bright(st, lcfg) else None
            self.dim_since = (self.dim_since or now) if P._is_dim(st, lcfg) else None

            with self.desk.lock:
                names = [p.name for p in (self.profiles.get(pid) for pid in self.desk.objects) if p]
            for n in names:
                if n not in self.d['objects_seen']:
                    self.d['objects_seen'].append(n)
            self._dirty = True
            due_save = now - self._last_save >= 30
        for habit, action, text, extra in say:
            self._say(habit, action, text, now, **extra)
        if due_save:
            self.save()

    # ── things that happened (camera or buttons) ──
    def _log_break_locked(self, now, seconds, how):
        mins = max(1, int(seconds // 60))
        self.d['breaks'] += 1
        self.d['last_break_ts'] = now
        self.d['break_log'].append({'ts': now, 'minutes': mins, 'how': how})
        self.d['break_log'] = self.d['break_log'][-50:]
        self._dirty = True
        text = (f'Nice, you were away for {mins} min. Break logged automatically!' if how != 'button'
                else 'Break logged. Roll your shoulders while you are at it.')
        return ('breaks', 'break', text, {'minutes': mins, 'how': how, 'count': self.d['breaks']})

    def _log_sip_locked(self, now, ml, how):
        self.d['sips'] += 1
        self.d['water_ml'] = int(self.d['water_ml'] + ml)
        self.d['last_sip_ts'] = now
        self.d['drinks'].append({'ts': now, 'ml': int(ml), 'how': how})
        self.d['drinks'] = self.d['drinks'][-100:]
        self._dirty = True
        goal = int((self.cfg.get('hydration') or {}).get('daily_goal_ml', 2400))
        text = (f'Sip detected! That\'s {self.d["sips"]} today (about {self.d["water_ml"] / 1000:.1f} of '
                f'{goal / 1000:.1f} L). Keep it up!') if how == 'camera' else \
               f'Logged! About {self.d["water_ml"] / 1000:.1f} of {goal / 1000:.1f} L today.'
        return ('hydration', 'sip', text, {'ml': int(ml), 'total_ml': self.d['water_ml'], 'how': how})

    def log(self, habit: str, action: str, **kw) -> dict:
        """Buttons in the app and action tags in chat: log_sip, log_glass, log_break, sunlight, food."""
        now = self.clock()
        with self._lock:
            self._roll_day(now)
            hyd = self.cfg.get('hydration') or {}
            if habit == 'hydration' and action == 'sip':
                item = self._log_sip_locked(now, float(kw.get('ml') or hyd.get('ml_per_sip', 80)), kw.get('how', 'button'))
            elif habit == 'hydration' and action == 'glass':
                item = self._log_sip_locked(now, float(kw.get('ml') or 240), kw.get('how', 'button'))
            elif habit == 'breaks' and action == 'break':
                item = self._log_break_locked(now, float(kw.get('minutes') or 5) * 60, 'button')
                self.sit_since = now
            elif habit == 'light' and action == 'sunlight':
                mins = int(kw.get('minutes') or 10)
                t = self.day_times(now)
                afternoon = now >= t['sunset_ts'] - 4 * 3600
                self.d['afternoon_light_ts' if afternoon else 'sunlight_ts'] = now
                self.d['sunlight_minutes'] += mins
                self._dirty = True
                item = ('light', 'sunlight', f'Logged {mins} minutes of {"late-afternoon" if afternoon else "morning"} '
                        f'sunlight. Your body clock says thanks!', {'minutes': mins, 'afternoon': afternoon})
            elif habit == 'food' and action == 'food':
                label = str(kw.get('label') or 'snack')[:60]
                healthy = bool(kw.get('healthy'))
                self.d['food'].append({'label': label, 'healthy': healthy, 'ts': now, 'how': 'button'})
                self._dirty = True
                item = ('food', 'logged', f'Logged {label}.', {'label': label, 'healthy': healthy})
            elif habit == 'food' and action == 'remove':
                i = int(kw.get('index', -1))
                if 0 <= i < len(self.d['food']):
                    self.d['food'].pop(i)
                    self._dirty = True
                item = None
            else:
                raise ValueError(f'unknown habit log: {habit}/{action}')
        if item:
            h, a, text, extra = item
            self._say(h, a, text, now, quiet=bool(kw.get('quiet')), **extra)
        self.save()
        return self.today(now)

    def bump(self, key: str, amount: int = 1) -> None:
        """Daily counters other parts update (alerts, lost-item alerts, nudges)."""
        with self._lock:
            self._roll_day(self.clock())
            self.d[key] = self.d.get(key, 0) + amount
            self._dirty = True

    def note_fired(self, nudge, now: float) -> None:
        with self._lock:
            self.last_fire[nudge.key] = now
            self.last_fire[f'habit:{nudge.habit}'] = now
            self.d['nudges_sent'] += 1
            self._dirty = True

    # ── reading ──
    def rule_state(self, now: float | None = None, open_todos=None) -> dict:
        """The snapshot the Protocols rules look at."""
        now = now if now is not None else self.clock()
        t = self.day_times(now)
        with self._lock:
            with self.desk.lock:
                session_start = self.desk.session_start
            return {
                'at_desk': self.at_desk(now), 'session_start': session_start, **t,
                'water_ml': self.d['water_ml'], 'sips': self.d['sips'], 'last_sip_ts': self.d['last_sip_ts'],
                'drink_on_desk': self.drink_on_desk,
                'sit_since': self.sit_since, 'last_break_ts': self.d['last_break_ts'],
                'meal_done_ts': self.meal_done_ts, 'meal_name': self.meal_name,
                'food_since': self.food_since, 'food_name': self.food_name,
                'phone_on_desk_since': self.phone_on_desk_since if self.phone_counts else None,
                'phone_counts': self.phone_counts,
                'sunlight_ts': self.d['sunlight_ts'], 'afternoon_light_ts': self.d['afternoon_light_ts'],
                'lux': self.light.get('lux'), 'brightness': self.light.get('brightness'),
                'bright_since': self.bright_since, 'dim_since': self.dim_since,
                'screen_on_desk': self.screen_on_desk,
                'open_todos': list(open_todos or []),
            }

    def nudges(self, now: float | None = None, open_todos=None) -> list:
        now = now if now is not None else self.clock()
        return P.candidate_nudges(self.rule_state(now, open_todos), self.cfg, now)

    def today(self, now: float | None = None) -> dict:
        """Everything the Habits page and the trackers show."""
        now = now if now is not None else self.clock()
        t = self.day_times(now)
        hyd = self.cfg.get('hydration') or {}
        window = float(hyd.get('goal_window_hours', 10)) * 3600
        goal = float(hyd.get('daily_goal_ml', 2400))
        with self._lock:
            d = json.loads(json.dumps(self.d))
            sit = round(now - self.sit_since) if (self.sit_since and self.at_desk(now)) else 0
            streak = round(self.phone_free_streak)
            phone_now = self.presence.is_present('phone') if self.phone_counts else None
            light = dict(self.light)
        pf = self.cfg.get('phone_free') or {}
        return {
            'day': d['day'], 'times': t,
            'hydration': {'sips': d['sips'], 'water_ml': d['water_ml'], 'goal_ml': int(goal),
                          'on_waking_ml': int(hyd.get('on_waking_ml', 600)),
                          'pace_ml': int(goal * min(1.0, max(0.0, (now - t['wake_ts']) / window))),
                          'last_sip_ts': d['last_sip_ts'], 'ml_per_sip': int(hyd.get('ml_per_sip', 80))},
            'breaks': {'count': d['breaks'], 'last_break_ts': d['last_break_ts'], 'sitting_sec': sit,
                       'longest_sit_sec': d['longest_sit_sec'],
                       'sit_limit_min': int((self.cfg.get('breaks') or {}).get('sit_limit_min', 30)),
                       'log': d['break_log'][-10:]},
            'phone_free': {'counted': self.phone_counts, 'phone_free_sec': round(d['phone_free_sec']),
                           'phone_on_desk_sec': round(d['phone_on_desk_sec']), 'streak_sec': streak,
                           'best_sec': d['best_phone_free_sec'], 'phone_on_desk': phone_now,
                           'goal_min': int(pf.get('goal_min_per_day', 120))},
            'light': {'sunlight_ts': d['sunlight_ts'], 'afternoon_light_ts': d['afternoon_light_ts'],
                      'sunlight_minutes': d['sunlight_minutes'], 'lux': light.get('lux'),
                      'brightness': light.get('brightness')},
            'food': {'entries': d['food']},
            'desk_sec': round(d['desk_sec']),
            'danger_count': d['danger_count'], 'lost_alerts': d['lost_alerts'],
            'hourly_activity': [round(x) for x in d['hourly_activity']],
            'objects_seen': d['objects_seen'],
        }
