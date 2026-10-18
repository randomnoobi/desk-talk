"""
Optional vision habit check with Ollama (only when OLLAMA_URL is set).

About every 42 seconds (settings → vision_habits.every_sec) a vision model
looks at the desk picture for posture, screen distance and a phone in hand.
Slouching stretches and nail biting go in the habit log; a nudge needs the
same thing seen twice in a row and waits 20+ minutes before repeating.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid

from ..events.stream import ALERT
from ..util import local_day
from .prompts import HABIT_SCENE_VLM_PROMPT


class HabitSceneVlmThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name='HabitSceneVlm')
        self.app = app
        s = app.settings.vision_habits
        self.every = max(18.0, float(s.get('every_sec', 42)))
        self.slouch_min = float(s.get('slouch_log_min_sec', 90))
        self.nail_cd = float(s.get('nail_log_cooldown_sec', 50))
        self.max_events = int(s.get('max_log_events', 120))
        self._prev_seen = {}

    # ── the habit log (Habits page → Camera log) ──
    def _log_locked(self, category, title, detail='', duration_sec=None, snapshot_id=None):
        desk = self.app.desk
        ev = {'id': uuid.uuid4().hex[:10], 'ts': time.time(), 'category': category, 'title': title,
              'detail': (detail or '')[:500]}
        if duration_sec is not None:
            ev['duration_sec'] = int(duration_sec)
        if snapshot_id:
            ev['snapshot_id'] = snapshot_id
        desk.habit_events.insert(0, ev)
        desk.habit_events = desk.habit_events[:self.max_events]

    def summary(self) -> dict:
        desk = self.app.desk
        day = local_day()
        with desk.lock:
            posture = sum(int(e.get('duration_sec') or 0) for e in desk.habit_events
                          if e.get('category') == 'posture_bad' and local_day(e['ts']) == day)
            nails = sum(1 for e in desk.habit_events if e.get('category') == 'nail_biting' and local_day(e['ts']) == day)
            ongoing = max(0.0, time.time() - desk.vlm_posture_slouch_since) if desk.vlm_posture_slouch_since else 0
        return {'posture_bad_seconds_today': int(posture + ongoing), 'nail_biting_count_today': nails,
                'ongoing_slouch_seconds': int(ongoing)}

    def apply(self, j: dict, now: float) -> None:
        """Update the posture segment + nail-biting log from one vision answer."""
        desk = self.app.desk
        posture = str(j.get('posture') or '').lower()
        nail = bool(j.get('nail_biting') or j.get('nails_hand_to_mouth'))
        posture_snap, nail_snap = None, False
        with desk.lock:
            if not desk.active_scenarios.get('reminders', True):
                desk.vlm_prev_nail_biting = nail
                return
            if posture == 'slouch' and desk.vlm_posture_slouch_since is None:
                desk.vlm_posture_slouch_since = now
            elif posture == 'good':
                start, desk.vlm_posture_slouch_since = desk.vlm_posture_slouch_since, None
                if start is not None and now - start >= self.slouch_min:
                    posture_snap = (max(1, round((now - start) / 60.0)), int(now - start))
            if nail and not desk.vlm_prev_nail_biting and now - desk.habit_nail_last_event_ts >= self.nail_cd:
                desk.habit_nail_last_event_ts = now
                nail_snap = True
            desk.vlm_prev_nail_biting = nail
        if posture_snap:
            mins, dur = posture_snap
            sid = desk.save_snapshot(label=f'Posture: ~{mins}m slouch')
            with desk.lock:
                self._log_locked('posture_bad', f'Poor posture ~{mins} min',
                                 'Desk cam (Ollama vision) tracked a slouched stretch until posture looked neutral again.',
                                 duration_sec=dur, snapshot_id=sid)
        if nail_snap:
            sid = desk.save_snapshot(label='Habit: nail biting')
            with desk.lock:
                self._log_locked('nail_biting', 'Nail biting noticed',
                                 'Vision pass flagged nail biting or hand-to-mouth near nails.', snapshot_id=sid)

    def _cooldown_fire(self, key, gap):
        now = time.time()
        with self.app.desk.lock:
            if now - float(self.app.desk.vlm_habit_alert_ts.get(key, 0) or 0) < gap:
                return False
            self.app.desk.vlm_habit_alert_ts[key] = now
        return True

    def tick(self):
        app = self.app
        if app.llm.vision_client is None:
            return
        with app.desk.lock:
            if not app.desk.active_scenarios.get('reminders', True):
                return
            jpg = app.desk.first_raw_frame()
        if not jpg:
            return
        raw = app.llm.vision(jpg, HABIT_SCENE_VLM_PROMPT)
        m = re.search(r'\{[\s\S]*\}', raw or '')
        try:
            j = json.loads(m.group(0) if m else (raw or ''))
        except ValueError:
            return
        now = time.time()
        with app.desk.lock:
            app.desk.vlm_habit_state = j
            app.desk.vlm_habit_ts = now
            app.desk.habit_vlm_brief = ', '.join(f'{k}={v}' for k, v in j.items() if k in ('posture', 'phone', 'attention'))
        self.apply(j, now)
        if not app.warm(now):
            return
        seen_now = {
            'too_close': str(j.get('face_screen_distance') or '').lower() == 'too_close',
            'slouch': str(j.get('posture') or '').lower() == 'slouch',
            'phone': str(j.get('phone') or '').lower() == 'in_hand' or str(j.get('attention') or '').lower() == 'phone',
        }
        prev, self._prev_seen = self._prev_seen, seen_now
        nudges = {
            'too_close': ('screens', 1200, "You've been leaning really close to my screen for a bit. "
                                           "Could you sit back a little? Your eyes will thank you."),
            'slouch': ('screens', 1500, "The camera has seen you slouching for a couple of minutes. "
                                        "Quick reset? Shoulders back, chin level."),
            'phone': ('phone_free', 1500, "I keep seeing your phone in your hand. If you're trying to focus, "
                                          "maybe put it in another room for 20 minutes?"),
        }
        from ..habits.reminders import speaker_for
        for key, (habit, gap, text) in nudges.items():
            if seen_now[key] and prev.get(key) and self._cooldown_fire(f'vlm_{key}', gap):
                speaker = speaker_for((habit, 'tasks'), app.roster.member_ids(), app.profiles)
                app.stream.publish(ALERT, kind='scenario', scenario='reminders', feature=f'vision_{key}',
                                   agent=speaker, text=text)
                break   # one nudge at a time

    def run(self):
        time.sleep(12)
        while True:
            try:
                self.tick()
            except Exception as exc:
                print(f'  [habit-vision] {exc!r}')
            time.sleep(self.every)
