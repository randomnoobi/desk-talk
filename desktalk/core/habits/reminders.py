"""
Evidence-based habit reminders.

Every reminders.check_every_sec this asks the rule engine, then the habit tracker, which nudges are
ready (no sip in 45 minutes, sitting for 30, phone on the desk ...). The rule engine's nudges come
first. If there is one, an agent in the chat that speaks for that habit says it in its own words, and
the message says what the camera saw. With no such agent, Lens (the camera) says the nudge's
ready-made text, without the AI.

Nothing is sent until the camera has watched for a while (reminders.warmup_min), during quiet
hours, while the camera is off, when nobody is at the desk, or sooner than reminders.min_gap_sec
after the last one.
"""

from __future__ import annotations

import logging

from desktalk.config import RemindersSettings
from desktalk.core.agents.speakers import speaker_for
from desktalk.domain import LENS, Nudge, NudgeSent
from desktalk.ports import ChatVoice, DeskStatus, EventBus, HabitsView, Notifier, Profiles, RosterView, RuleNudges

log = logging.getLogger(__name__)


class HabitReminders:
    """A PeriodicWorker: one round = maybe one nudge."""

    name = 'habit reminders'

    def __init__(self, habits: HabitsView, rules: RuleNudges, status: DeskStatus, roster: RosterView,
                 profiles: Profiles, voice: ChatVoice, notifier: Notifier, bus: EventBus,
                 settings: RemindersSettings):
        self._habits = habits
        self._rules = rules
        self._status = status
        self._roster = roster
        self._profiles = profiles
        self._voice = voice
        self._notifier = notifier
        self._bus = bus
        self._every = settings.check_every_sec
        self._min_gap = settings.min_gap_sec
        self._last_sent: float | None = None

    def tick(self, now: float) -> float:
        if not self._status.reminders_ready(now) or not self._status.user_active(now):
            return self._every
        if self._last_sent is not None and now - self._last_sent < self._min_gap:
            return self._every
        nudges = [*self._rules.nudges(now), *self._habits.nudges(now, self._status.open_todos())]
        if not nudges:
            return self._every
        nudge = nudges[0]
        self._rules.note_fired(nudge, now)
        self._habits.note_fired(nudge, now)
        self.say(nudge, now)
        self._last_sent = now
        return self._every

    def say(self, nudge: Nudge, now: float) -> None:
        """Say one nudge now (ReminderSender): by an agent in the chat that speaks for its habit, in its own
        words, or by Lens with the nudge's own text. tick() uses it for a due nudge; the developer page's Demo
        tab for a pretend one, which nothing waits for."""
        speaker = speaker_for(nudge.speakers, self._roster.member_ids(), self._profiles)
        text = self._voice.phrase_nudge(speaker, nudge) if speaker != LENS else None    # Lens never uses the AI
        text = text or nudge.text
        self._notifier.send({'type': 'group_message', 'character': speaker, 'text': text, 'habit': nudge.habit,
                             'toast': True, 'notify': 'reminder'})
        self._bus.publish(NudgeSent(ts=now, habit=nudge.habit, rule=nudge.key, agent=speaker,
                                    evidence=nudge.evidence, text=text))
        log.info('%s nudged about %s: %s', speaker, nudge.habit, nudge.evidence)
