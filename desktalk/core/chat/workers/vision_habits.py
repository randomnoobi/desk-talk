"""
Vision habit checks, with a vision AI on this computer (Ollama, only when OLLAMA_URL is set).

About every vision_habits.every_sec the AI looks at the newest desk picture for posture, screen
distance and a phone in hand. Long slouching stretches and nail biting go in the habit log
(Habits page → Camera log). A nudge needs the same thing seen twice in a row, and the same nudge
waits vision_habits.repeat_after_sec before it's sent again.

    vision_habits = VisionHabits(ollama_or_none, bus, desk, roster, profiles, desk_status, config.vision_habits, clock,
                                 proof_photos)
    supervisor.add(vision_habits, start_after=config.vision_habits.start_delay_sec)
    vision_habits.summary(), .events(), .latest()       # for the web routes
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from desktalk.config import VisionHabitsSettings
from desktalk.core.chat import prompts
from desktalk.core.chat.workers.ai import look
from desktalk.core.chat.workers.speakers import speaker_for
from desktalk.core.chat.workers.text import json_in
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.desk_state import DeskState
from desktalk.domain import Alert
from desktalk.ports import ChatModel, Clock, DeskStatus, EventBus, Profiles, RosterView
from desktalk.util import local_day

log = logging.getLogger(__name__)

BRIEF_KEYS = ('posture', 'phone', 'attention')      # what the chat's AI is told about the last look
# Nudges, in order of importance: what was seen → (the habit whose agent says it, the text)
NUDGES = {
    'too_close': ('screens', "You've been leaning really close to my screen for a bit. "
                             "Could you sit back a little? Your eyes will thank you."),
    'slouch': ('screens', "The camera has seen you slouching for a couple of minutes. "
                          "Quick reset? Shoulders back, chin level."),
    'phone': ('phone_free', "I keep seeing your phone in your hand. If you're trying to focus, "
                            "maybe put it in another room for 20 minutes?"),
}


def _seen(answer: dict[str, Any]) -> dict[str, bool]:
    def word(key: str) -> str:
        return str(answer.get(key) or '').lower()
    return {'too_close': word('face_screen_distance') == 'too_close',
            'slouch': word('posture') == 'slouch',
            'phone': word('phone') == 'in_hand' or word('attention') == 'phone'}


class VisionHabits:
    """A PeriodicWorker. `vision` is the AI that looks at pictures, or None when no local vision AI is set up
    (the checks are then skipped, as before: a cloud AI's quota is kept for the chat)."""

    name = 'vision habits'

    def __init__(self, vision: ChatModel | None, bus: EventBus, desk: DeskState, roster: RosterView,
                 profiles: Profiles, status: DeskStatus, settings: VisionHabitsSettings, clock: Clock,
                 photos: ProofPhotos):
        self._vision = vision
        self._bus = bus
        self._desk = desk
        self._roster = roster
        self._profiles = profiles
        self._status = status
        self._settings = settings
        self._clock = clock
        self._photos = photos
        self._every = max(settings.min_every_sec, settings.every_sec)
        self._lock = threading.Lock()                # the web routes read the log and the summary
        self._latest: dict[str, Any] = {}
        self._latest_ts = 0.0
        self._log: list[dict[str, Any]] = []         # posture / nail-biting log, newest first
        self._slouch_since: float | None = None
        self._nails_before = False
        self._nails_logged_at: float | None = None
        self._seen_before: dict[str, bool] = {}
        self._nudged_at: dict[str, float] = {}

    # ── for the web routes ──
    def summary(self) -> dict[str, int]:
        """Today's slouching (seconds, including a slouch going on right now) and nail biting (times)."""
        now = self._clock.now()
        today = local_day(now)
        with self._lock:
            posture = sum(int(e.get('duration_sec') or 0) for e in self._log
                          if e['category'] == 'posture_bad' and local_day(e['ts']) == today)
            nails = sum(1 for e in self._log if e['category'] == 'nail_biting' and local_day(e['ts']) == today)
            ongoing = max(0.0, now - self._slouch_since) if self._slouch_since is not None else 0.0
        return {'posture_bad_seconds_today': int(posture + ongoing), 'nail_biting_count_today': nails,
                'ongoing_slouch_seconds': int(ongoing)}

    def events(self) -> list[dict[str, Any]]:
        """The habit log, newest first (copies)."""
        with self._lock:
            return [dict(e) for e in self._log]

    def latest(self) -> tuple[dict[str, Any], float]:
        """The AI's last answer and when it came (({}, 0.0) before the first)."""
        with self._lock:
            return dict(self._latest), self._latest_ts

    # ── one round ──
    def tick(self, now: float) -> float:
        if self._vision is None:
            return self._every
        with self._desk.lock:
            on = self._desk.active_scenarios.get('reminders', True)
            jpeg = self._desk.first_raw_frame() if on else None
        answer = json_in(look(self._vision, jpeg, prompts.HABIT_SCENE_VLM_PROMPT, background=True,
                              what='the posture check'))
        if answer is None:
            return self._every
        brief = ', '.join(f'{k}={v}' for k, v in answer.items() if k in BRIEF_KEYS)
        with self._lock:
            self._latest, self._latest_ts = dict(answer), now
        with self._desk.lock:
            self._desk.vlm_habit_state = dict(answer)
            self._desk.vlm_habit_ts = now
            self._desk.vision_habit_brief = brief
        self._log_habits(answer, now)
        self._maybe_nudge(answer, now)
        return self._every

    def _log_habits(self, answer: dict[str, Any], now: float) -> None:
        """Start or end a slouching stretch, and note nail biting when it starts."""
        posture = str(answer.get('posture') or '').lower()
        nails = bool(answer.get('nail_biting') or answer.get('nails_hand_to_mouth'))
        slouched = None
        with self._lock:
            if posture == 'slouch' and self._slouch_since is None:
                self._slouch_since = now
            elif posture == 'good' and self._slouch_since is not None:
                start, self._slouch_since = self._slouch_since, None
                if now - start >= self._settings.slouch_log_min_sec:
                    slouched = int(now - start)
            nails_started = (nails and not self._nails_before and (
                self._nails_logged_at is None or now - self._nails_logged_at >= self._settings.nail_log_cooldown_sec))
            if nails_started:
                self._nails_logged_at = now
            self._nails_before = nails
        if slouched is not None:
            minutes = max(1, round(slouched / 60))
            photo = self._photos.save(f'Posture: ~{minutes}m slouch')
            self._add('posture_bad', f'Poor posture ~{minutes} min', now, duration_sec=slouched, snapshot_id=photo,
                      detail='The desk camera saw a slouched stretch until your posture looked neutral again.')
        if nails_started:
            photo = self._photos.save('Habit: nail biting')
            self._add('nail_biting', 'Nail biting noticed', now, snapshot_id=photo,
                      detail='The vision check saw nail biting, or a hand at your mouth near your nails.')

    def _add(self, category: str, title: str, now: float, *, detail: str, duration_sec: int | None = None,
             snapshot_id: str | None = None) -> None:
        entry: dict[str, Any] = {'id': uuid.uuid4().hex[:10], 'ts': now, 'category': category, 'title': title,
                                 'detail': detail}
        if duration_sec is not None:
            entry['duration_sec'] = duration_sec
        if snapshot_id:
            entry['snapshot_id'] = snapshot_id
        with self._lock:
            self._log.insert(0, entry)
            del self._log[self._settings.max_log_events:]
        log.info('Habit log: %s', title)

    def _maybe_nudge(self, answer: dict[str, Any], now: float) -> None:
        seen = _seen(answer)
        before, self._seen_before = self._seen_before, seen
        if not self._status.warm(now):
            return
        repeats = self._settings.repeat_after_sec
        for key, (habit, text) in NUDGES.items():
            last = self._nudged_at.get(key)
            if not (seen[key] and before.get(key)) or (last is not None and now - last < getattr(repeats, key)):
                continue
            self._nudged_at[key] = now
            speaker = speaker_for((habit, 'tasks'), self._roster.member_ids(), self._profiles)
            self._bus.publish(Alert(ts=now, kind='scenario', scenario='reminders', feature=f'vision_{key}',
                                    agent=speaker, text=text))
            return      # one nudge at a time
