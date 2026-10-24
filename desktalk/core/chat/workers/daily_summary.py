"""
The end-of-day recap: once a day at settings → daily_summary.hour, an agent in the chat (or Lens)
sums up the day: water, breaks, phone-free time, sunlight, near-spills, lost items. It is only sent
if the camera saw something that day, and only once a day, even if the app restarts.

    recap = DailySummary(chat_model, notifier, roster, habits, store, config.daily_summary, config.llm.replies,
                         default_temperature=config.agents_file.default.temperature, clock=clock,
                         prompts=config.prompts.daily_recap, preview_chars=config.logging.preview_chars)
    supervisor.add(recap, start_after=config.daily_summary.start_delay_sec)
"""

from __future__ import annotations

import logging
import random
from typing import Any

from desktalk.config import AiReplies, DailySummarySettings
from desktalk.config.prompts import DailyRecapPrompts
from desktalk.core.agents.speakers import LENS
from desktalk.core.chat import prompts as fill
from desktalk.core.chat.workers.ai import ask
from desktalk.ports import ChatModel, Clock, HabitsView, KeyValueStore, Notifier, RosterView

log = logging.getLogger(__name__)

SENT_KEY = 'daily_summary'          # in the KeyValueStore: {"sent_day": "2026-10-21"}


def recap_facts(stats: dict[str, Any], objects_listed: int) -> str:
    """Today's numbers (HabitsView.today()) as short sentences for the AI, or for the built-in recap."""
    facts = []
    stretches: list[tuple[int, int, float]] = []          # (from hour, to hour, activity) of busy hours in a row
    for hour, activity in enumerate(stats.get('hourly_activity') or []):
        if activity <= 0:
            continue
        if stretches and stretches[-1][1] == hour:
            start, _, total = stretches[-1]
            stretches[-1] = (start, hour + 1, total + activity)
        else:
            stretches.append((hour, hour + 1, activity))
    if stretches:
        start, end, _ = max(stretches, key=lambda s: s[2])
        facts.append(f'Busiest period: between {start}:00 and {end}:00.')
    water, breaks, phone_free = stats['hydration'], stats['breaks'], stats['phone_free']
    facts.append(f'Water: about {water["water_ml"] / 1000:.1f} of {water["goal_ml"] / 1000:.1f} L '
                 f'({water["sips"]} sips).')
    facts.append(f'Breaks taken: {breaks["count"]}. Longest stretch sitting: {int(breaks["longest_sit_sec"] // 60)} '
                 f'minutes.')
    if phone_free.get('counted'):
        facts.append(f'Phone-free desk time: {int(phone_free["phone_free_sec"] // 60)} minutes '
                     f'(best streak {int(phone_free["best_sec"] // 60)}).')
    facts.append('Morning sunlight: logged.' if stats['light'].get('sunlight_ts') else 'Morning sunlight: not logged.')
    if stats.get('desk_sec'):
        facts.append(f'Total time at desk: ~{int(stats["desk_sec"] // 60)} minutes.')
    if stats.get('danger_count'):
        facts.append(f'Near-spill or danger alerts: {stats["danger_count"]}.')
    if stats.get('lost_alerts'):
        facts.append(f'Lost-object alerts: {stats["lost_alerts"]}.')
    if stats.get('objects_seen') and objects_listed:
        facts.append(f'Objects seen today: {", ".join(stats["objects_seen"][:objects_listed])}.')
    return ' '.join(facts)


class DailySummary:
    """A PeriodicWorker: checks every daily_summary.check_every_sec whether it's time for the recap."""

    name = 'daily recap'

    def __init__(self, chat: ChatModel, notifier: Notifier, roster: RosterView, habits: HabitsView,
                 store: KeyValueStore, settings: DailySummarySettings, replies: AiReplies, *,
                 default_temperature: float, clock: Clock, prompts: DailyRecapPrompts, preview_chars: int,
                 rng: random.Random | None = None):
        """`replies`: settings → llm.replies (this uses daily_recap). `default_temperature`: agents.yaml's default
        temperature, used if daily_recap has none. `store` remembers the day of the last recap. `clock` gives the
        local hour. `prompts`: config/prompts.yaml → daily_recap. `preview_chars`: settings →
        logging.preview_chars, the longest recap quoted in the log."""
        self._chat = chat
        self._notifier = notifier
        self._roster = roster
        self._habits = habits
        self._store = store
        self._settings = settings
        self._reply = replies.daily_recap
        self._default_temperature = default_temperature
        self._clock = clock
        self._words = prompts
        self._preview_chars = preview_chars
        self._rng = rng or random.Random()
        self._sent_day: str | None = None      # kept here too, in case saving it fails

    def tick(self, now: float) -> float:
        if self._clock.local_hour(now) == self._settings.hour:
            stats = self._habits.today(now)
            if stats.get('objects_seen') and not self._sent_on(stats['day']):
                self._send(stats)
        return self._settings.check_every_sec

    def _sent_on(self, day: str) -> bool:
        return self._sent_day == day or (self._store.load(SENT_KEY) or {}).get('sent_day') == day

    def _send(self, stats: dict[str, Any]) -> None:
        facts = recap_facts(stats, self._settings.objects_listed)
        messages = [{'role': 'system', 'content': self._words.system},
                    {'role': 'user', 'content': fill.daily_summary_user(self._words, facts)}]
        text = ask(self._chat, messages, self._reply, own_temperature=self._default_temperature, background=True,
                   what='the daily recap')
        text = text or f"Today's desk recap: {facts}"
        members = self._roster.member_ids()
        speaker = self._rng.choice(members) if members else LENS
        self._notifier.send({'type': 'group_message', 'character': speaker, 'text': text, 'recap': True})
        self._sent_day = stats['day']
        log.info('Daily recap from %s: %s', speaker, text[:self._preview_chars])
        try:
            self._store.save(SENT_KEY, {'sent_day': stats['day']})
        except OSError as exc:
            log.warning('Could not remember that the recap went out today (%s): it may be sent again after a '
                        'restart', exc)
