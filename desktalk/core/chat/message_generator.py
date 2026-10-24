"""
The chat: turns events into messages, puts habit reminders into words, and answers you.

It listens on the event bus:
    AgentJoined / AgentLeft  → "Cup joined the chat" (+ a hello, and an AI personality for a new kind of object)
    Alert                    → an agent warns about what the camera saw (alert_voice.py)
    HabitEvent               → "Sip detected! That's 3 today."
phrase_nudge() puts a habit reminder into an agent's words (nudge_voice.py), and answer() replies to what
you type, 1-on-1 or in the group (chat_answers.py, for /api/chat). What the agents remember is kept by
memory.py.

When the AI is unavailable the characters still speak: alerts fall back to the agent's own
too_close line, and reminders to the nudge's ready-made text. Your own messages get a plain
reason instead of a reply.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from desktalk.config import AgentsSettings, ChatSettings, LlmSettings
from desktalk.config.prompts import Prompts
from desktalk.core.agents.speakers import speaker_for
from desktalk.core.chat import prompts as fill
from desktalk.core.chat.agent_ai import AgentAI
from desktalk.core.chat.alert_voice import AlertVoice
from desktalk.core.chat.chat_answers import ChatAnswers
from desktalk.core.chat.live_context import build_live_context
from desktalk.core.chat.memory import ChatMemory, TrackerPanels
from desktalk.core.chat.nudge_voice import NudgeVoice
from desktalk.domain import AgentJoined, AgentLeft, Alert, Event, HabitEvent, Nudge
from desktalk.ports import (
    CharacterMemory,
    ChatModel,
    Clock,
    DeskScene,
    DeskStatus,
    EventBus,
    HabitLog,
    HabitsView,
    Notifier,
    Profiles,
    RosterView,
    SafetyBoard,
)

__all__ = ['ChatDesk', 'MessageGenerator']


@runtime_checkable
class ChatDesk(CharacterMemory, DeskScene, SafetyBoard, Protocol):
    """What the chat reads and writes on the desk: what the agents remember, what the camera sees and the
    dangers. (bootstrap passes WholeDesk from desktalk/core/desk/, which is all three.)"""


class MessageGenerator:
    """The ChatVoice (desktalk/ports.py), and the chat's voice for events and the user's messages.

    Settings: llm (reply sizes, memory_per_agent, chat_history_turns), chat, agents (announce,
    greet_on_join, greet_again_after_min, invent_personality), sunlight_default_min (habit_tracker),
    reply_max_chars (longest AI reply kept), prompts (config/prompts.yaml), drink_labels (safety.liquids:
    the drinks when no agent speaks for hydration), preview_chars (logging: the longest reply quoted in
    the log). local_model: the AI is a small model on this computer (Ollama), which gets shorter prompts
    and fewer earlier messages."""

    def __init__(self, model: ChatModel, notifier: Notifier, roster: RosterView, profiles: Profiles,
                 habits: HabitsView, desk: ChatDesk, clock: Clock, *, status: DeskStatus, habit_log: HabitLog,
                 llm: LlmSettings, chat: ChatSettings, agents: AgentsSettings, sunlight_default_min: float,
                 reply_max_chars: int, prompts: Prompts, drink_labels: Iterable[str], preview_chars: int,
                 local_model: bool = False, rng: random.Random | None = None):
        self._notifier = notifier
        self._roster = roster
        self._profiles = profiles
        self._habits = habits
        self._desk = desk
        self._clock = clock
        self._status = status
        self._chat = chat
        self._agents = agents
        self._words = prompts
        self._greeted: dict[str, float] = {}                # agent id → last hello
        self._ai = AgentAI(model, profiles, llm, reply_max_chars)
        memory = ChatMemory(desk, keep=llm.memory_per_agent, reply_chars=chat.memory_reply_chars,
                            user_chars=chat.memory_user_chars, words=prompts.memory)
        self._alerts = AlertVoice(self._ai, profiles, desk, self.live_context, prompts.alert, chat, drink_labels)
        self._nudges = NudgeVoice(self._ai, profiles, habits, desk, memory, clock, prompts, chat, local=local_model)
        self._answers = ChatAnswers(self._ai, model, notifier, roster, profiles, habits, desk, memory,
                                    TrackerPanels(desk, chat), clock, self.live_context, habit_log=habit_log,
                                    words=prompts, llm=llm, chat=chat, sunlight_default_min=sunlight_default_min,
                                    preview_chars=preview_chars, local=local_model,
                                    most_speakers=agents.max_agents, rng=rng or random.Random())

    def subscribe_to(self, bus: EventBus) -> None:
        bus.subscribe('chat:roster', self.on_roster, types=(AgentJoined, AgentLeft))
        bus.subscribe('chat:alerts', self.on_alert, types=(Alert,))
        bus.subscribe('chat:habits', self.on_habit, types=(HabitEvent,))

    def live_context(self, user_message: str = '') -> dict[str, Any]:
        """Real-time desk facts for the AI."""
        now = self._clock.now()
        return build_live_context(self._desk, self._desk, profiles=self._profiles, members=self._roster.member_ids(),
                                  habits_today=self._habits.today(now), now=now,
                                  camera_on=self._status.camera_connected(), chat=self._chat,
                                  words=self._words.live_context, user_message=user_message)

    # ════════════════════════════════════════════════════════════════
    #  Events
    # ════════════════════════════════════════════════════════════════
    def on_roster(self, ev: Event) -> None:
        if not isinstance(ev, (AgentJoined, AgentLeft)):
            return
        a = self._profiles.get(ev.agent)
        if not a:
            return
        joined = isinstance(ev, AgentJoined)
        info = {**a.public(), 'on_desk': joined}
        self._notifier.send({'type': 'roster', 'action': 'joined' if joined else 'left', 'agent': info,
                             'text': f'{a.name} {"joined" if joined else "left"} the chat',
                             'quiet': not self._agents.announce})
        if not joined:
            return
        if a.auto and not a.learned and self._agents.invent_personality:
            text = self._ai.ask(fill.invent_personality(self._words.personality, a.object), 'personality', a,
                                background=True)
            if text and len(text.split()) >= self._chat.personality_min_words:
                self._profiles.set_personality(a.id, text)
        now = self._clock.now()
        hello = a.hello_text()
        if (self._agents.greet_on_join and hello
                and now - self._greeted.get(a.id, float('-inf')) >= self._agents.greet_again_after_min * 60):
            self._greeted[a.id] = now
            self._notifier.send({'type': 'group_message', 'character': a.id, 'text': hello, 'hello': True})

    def on_habit(self, ev: Event) -> None:
        """Things the habit tracker noticed (a sip, a break, food, a phone-free streak)."""
        if not isinstance(ev, HabitEvent):
            return
        self._notifier.send({'type': 'habit_update', 'habit': ev.habit, 'action': ev.action})
        if not ev.say or ev.quiet:
            return
        speaker = speaker_for((ev.habit,), self._roster.member_ids(), self._profiles)
        self._notifier.send({'type': 'group_message', 'character': speaker, 'text': ev.say, 'habit': ev.habit})

    def on_alert(self, ev: Event) -> None:
        if not isinstance(ev, Alert):
            return
        msg = self._alerts.message_for_alert(ev)
        if msg:
            self._notifier.send(msg)

    # ════════════════════════════════════════════════════════════════
    #  Habit reminders and answering you
    # ════════════════════════════════════════════════════════════════
    def phrase_nudge(self, agent_id: str, nudge: Nudge) -> str | None:
        """The agent's words for a habit reminder: the AI's, or the nudge's own text when the AI can't help."""
        return self._nudges.phrase_nudge(agent_id, nudge)

    def answer(self, chat_id: str, message: str, history: Any = None, widget: Any = None) -> dict[str, Any]:
        """The reply body for /api/chat: see ChatAnswers.answer. Raises UnknownChat for an unknown agent."""
        return self._answers.answer(chat_id, message, history, widget)

    def save_widget(self, pid: str, raw: Any) -> bool:
        """Keep the to-do list the app sent for an agent's panel. False if the agent or the list isn't valid."""
        return self._answers.save_widget(pid, raw)

    def note_panel_event(self, pid: str, action: Any) -> bool:
        """The user opened (or used) an agent's tracker panel. False (nothing kept) for an unknown agent."""
        return self._answers.note_panel_event(pid, action)
