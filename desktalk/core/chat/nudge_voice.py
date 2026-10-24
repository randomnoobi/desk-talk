"""
Habit reminders in an agent's voice. The habit rules decide when a reminder is due (and give its reason
and the book's tip); this asks the AI to say it as the agent, with the agent's tracker and the last
posture check. When the AI can't help, the reminder's own ready-made text is used.
"""

from __future__ import annotations

from desktalk.config import ChatSettings
from desktalk.config.prompts import Prompts
from desktalk.core.chat import prompts
from desktalk.core.chat.agent_ai import AgentAI
from desktalk.core.chat.memory import ChatMemory
from desktalk.core.chat.replies import looks_cut_off
from desktalk.core.chat.tracker_actions import tracker_prompt
from desktalk.domain import AgentProfile, Nudge
from desktalk.ports import CharacterMemory, Clock, HabitsView, Profiles


class NudgeVoice:
    """`desk`: the tracker panels and the last posture check are read from it. `local`: the AI is a small model
    on this computer (Ollama), which gets the shorter prompt."""

    def __init__(self, ai: AgentAI, profiles: Profiles, habits: HabitsView, desk: CharacterMemory,
                 memory: ChatMemory, clock: Clock, words: Prompts, chat: ChatSettings, *, local: bool):
        self._ai = ai
        self._profiles = profiles
        self._habits = habits
        self._desk = desk
        self._memory = memory
        self._clock = clock
        self._words = words
        self._chat = chat
        self._local = local

    def phrase_nudge(self, agent_id: str, nudge: Nudge) -> str | None:
        """The agent's words for a habit reminder: the AI's, or the nudge's own text when the AI can't help."""
        a = self._profiles.get(agent_id)
        if a:
            text = self._ai_nudge(a, nudge)
            if text:
                self._memory.remember(agent_id, text, 'habit reminder')
                return text
        return nudge.text or None

    def _ai_nudge(self, a: AgentProfile, nudge: Nudge) -> str | None:
        now = self._clock.now()
        todo = self._desk.widget(a.id)
        vision_note = (self._desk.vision_brief() or '').strip()
        block = tracker_prompt(a.tracker, self._habits.today(now), todo, [], now, self._chat)
        signals = f'; vision note: {vision_note}' if vision_note else ''
        w = self._words
        system = prompts.habit_system(w.habit_reminder, a, prompts.habit_area(w.habit_areas, a), block,
                                      signals or 'none', nudge.evidence, nudge.tip, short=self._local)
        text = self._ai.ask([{'role': 'system', 'content': system},
                             {'role': 'user', 'content': 'Send a quick habit reminder right now.'}],
                            'nudge', a, background=True)
        if not text or looks_cut_off(text, self._chat.min_reply_words):
            return None
        return self._ai.one_speaker(text, a) or None
