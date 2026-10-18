"""
Evidence-based habit reminders.

Every 30 seconds this asks the Protocols rules (habits/protocols.py) whether
the camera has seen a reason to nudge (no sip in 45 minutes, sitting for 30,
phone on the desk...). If so, an agent that speaks for that habit says it,
and the message says what the camera saw.

Nothing is sent until the camera has watched for a while (settings →
reminders.warmup_min), during quiet hours, while the camera is off, when
nobody is at the desk, or sooner than reminders.min_gap_sec after the last one.
"""

from __future__ import annotations

import threading
import time

from ..events.stream import NUDGE
from ..util import in_hour_window

LENS = 'lens'


def speaker_for(habits, member_ids, profiles) -> str:
    """Which agent says a nudge: one in the chat that speaks for the habit, else Lens (the camera)."""
    members = list(member_ids)
    for habit in habits:
        for pid in members:
            p = profiles.get(pid)
            if p and habit in p.habits:
                return pid
    return LENS


def quiet_hours_now(prefs, now: float | None = None) -> bool:
    if not prefs.get('quiet_enabled'):
        return False
    hour = time.localtime(now if now is not None else time.time()).tm_hour
    return in_hour_window(hour, int(prefs.get('quiet_start_hour')), int(prefs.get('quiet_end_hour')))


class HabitReminderThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name='HabitReminders')
        self.app = app
        s = app.settings.reminders
        self.every = float(s.get('check_every_sec', 30))
        self.min_gap = float(s.get('min_gap_sec', 600))
        self.last_sent = 0.0

    def run(self):
        while True:
            time.sleep(self.every)
            try:
                self.tick(time.time())
            except Exception as exc:
                print(f'  [habit] Error: {exc!r}')

    def tick(self, now: float):
        app = self.app
        if not app.reminders_ready(now) or not app.user_active(now):
            return None
        if now - self.last_sent < self.min_gap:
            return None
        nudges = app.habits.nudges(now, app.open_todos())
        from .protocols import pick_nudge
        nudge = pick_nudge(nudges, app.habits.last_fire, now)
        if not nudge:
            return None
        speaker = speaker_for(nudge.speakers, app.roster.member_ids(), app.profiles)
        text = None
        if speaker != LENS:                      # Lens never uses the AI
            text = app.generator.phrase_nudge(speaker, nudge)
        text = text or nudge.text
        app.generator.broadcast({'type': 'group_message', 'character': speaker, 'text': text,
                                 'habit': nudge.habit, 'toast': True})
        app.habits.note_fired(nudge, now)
        app.stream.publish(NUDGE, ts=now, habit=nudge.habit, rule=nudge.key, agent=speaker,
                           evidence=nudge.evidence, text=text)
        self.last_sent = now
        print(f'  [habit] {speaker}: {nudge.evidence}')
        return nudge
