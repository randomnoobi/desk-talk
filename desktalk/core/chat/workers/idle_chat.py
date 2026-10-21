"""
Idle chat: now and then the agents on the desk make small talk with each other.

Every idle_chat.min_sec to max_sec, if someone is at the desk, reminders aren't waiting (warm-up,
camera off, quiet hours) and at least two agents are in the chat:
  1. the AI writes ONE small-talk topic from the desk and the agents' recent memories
  2. a random agent opens by asking the others a question about it
  3. a "director" AI decides whether the chat goes on and who speaks next; that agent adds one
     line, and so on, until the director ends it or idle_chat.max_turns replies have been said.

Each line is its own round, with a short pause before the next one (like someone typing), so the
Supervisor can stop the job at any moment. Nothing is said while you're typing: the chat waits
until you've stopped for idle_chat.quiet_after_typing_sec, and a chat in progress ends.

    idle = IdleChat(chat_model, notifier, desk_status, roster, profiles, desk, desk_summary, config.idle_chat,
                    config.llm.replies, memory_kept=config.llm.memory_per_agent,
                    memory_chars=config.chat.memory_reply_chars,
                    default_temperature=config.agents_file.default.temperature)
    supervisor.add(idle, start_after=config.idle_chat.start_delay_sec)
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from desktalk.config import AiReplies, IdleChatSettings
from desktalk.core.chat.workers import prompts
from desktalk.core.chat.workers.ai import ask
from desktalk.core.chat.workers.speakers import LENS
from desktalk.core.chat.workers.text import json_in, single_speaker_text, strip_action_tags
from desktalk.desk_state import DeskState
from desktalk.ports import ChatModel, DeskStatus, Notifier, Profiles, RosterView

log = logging.getLogger(__name__)


@dataclass
class _Talk:
    """A small-talk conversation in progress."""

    topic: str
    members: list[str]
    desk_summary: str
    recent: list[dict[str, str]] = field(default_factory=list)     # {'character', 'text'}, oldest first
    last_speaker: str = ''
    replies: int = 0


class IdleChat:
    """A PeriodicWorker. `desk_summary()` is the desk right now in words (what the chat's AI is told)."""

    name = 'idle chat'

    def __init__(self, chat: ChatModel, notifier: Notifier, status: DeskStatus, roster: RosterView,
                 profiles: Profiles, desk: DeskState, desk_summary: Callable[[], str], settings: IdleChatSettings,
                 replies: AiReplies, *, memory_kept: int, memory_chars: int, default_temperature: float,
                 rng: random.Random | None = None):
        """`replies`: settings → llm.replies (idle_topic, idle_line, idle_director). `memory_kept` /
        `memory_chars`: settings → llm.memory_per_agent / chat.memory_reply_chars, for the lines each agent
        remembers saying. `default_temperature`: agents.yaml's default, for the topic writer and the
        director when their settings have none."""
        self._chat = chat
        self._notifier = notifier
        self._status = status
        self._roster = roster
        self._profiles = profiles
        self._desk = desk
        self._desk_summary = desk_summary
        self._settings = settings
        self._replies = replies
        self._memory_kept = memory_kept
        self._memory_chars = memory_chars
        self._default_temperature = default_temperature
        self._rng = rng or random.Random()
        self._first_round = True
        self._talk: _Talk | None = None

    def tick(self, now: float) -> float:
        if self._first_round:            # as before: after the start delay, a full wait before the first chat
            self._first_round = False
            return self._until_next_chat()
        if self._talk is not None:
            return self._next_line(now, self._talk)
        typing = self._settings.quiet_after_typing_sec - self._since_typing(now)
        if typing > 0:
            return typing
        if not (self._status.reminders_ready(now) and self._status.user_active(now)) or not self._chat.ready:
            return self._until_next_chat()
        members = self._roster.member_ids()
        if len(members) < 2:
            return self._until_next_chat()
        return self._start(members)

    # ── the conversation ──
    def _start(self, members: Sequence[str]) -> float:
        summary = self._desk_summary()
        topic = self._topic(members, summary)
        if not topic:
            log.info('Small talk: no topic from the AI, skipping this round')
            return self._until_next_chat()
        talk = _Talk(topic=topic, members=list(members), desk_summary=summary)
        first = self._rng.choice(talk.members)
        line = self._line(first, talk)
        if not line:
            return self._until_next_chat()
        self._say(first, line, talk)
        if self._settings.max_turns <= 0:
            return self._until_next_chat()
        self._talk = talk
        return self._pause()

    def _next_line(self, now: float, talk: _Talk) -> float:
        if self._since_typing(now) < self._settings.quiet_after_typing_sec:
            log.info('Small talk: you started typing, so the chat stops here')
            return self._end()
        speaker = self._next_speaker(talk)
        line = self._line(speaker, talk) if speaker else None
        if not speaker or not line:
            return self._end()
        self._say(speaker, line, talk)
        talk.replies += 1
        if talk.replies >= self._settings.max_turns:
            return self._end()
        return self._pause()

    def _end(self) -> float:
        self._talk = None
        return self._until_next_chat()

    def _until_next_chat(self) -> float:
        return self._rng.randint(self._settings.min_sec, self._settings.max_sec)

    def _pause(self) -> float:
        return self._settings.pause_between_lines_sec + self._rng.random() * self._settings.pause_random_sec

    def _since_typing(self, now: float) -> float:
        with self._desk.lock:
            return now - self._desk.last_user_activity

    # ── asking the AI ──
    def _topic(self, members: Sequence[str], summary: str) -> str | None:
        user = (f'DESK STATUS:\n{summary}\n\nOBJECTS PRESENT: {self._names(members)}\n'
                f'RECENT MEMORY:\n{self._memories(members)}\n\nTopic:')
        text = ask(self._chat, [{'role': 'system', 'content': prompts.TOPIC_SYSTEM}, {'role': 'user', 'content': user}],
                   self._replies.idle_topic, own_temperature=self._default_temperature, background=True,
                   what='a small-talk topic')
        lines = (text or '').strip().splitlines()
        topic = lines[0].strip().strip('"\'').strip() if lines else ''
        return topic if topic and len(topic.split()) >= self._settings.topic_min_words else None

    def _line(self, agent_id: str, talk: _Talk) -> str | None:
        agent = self._profiles.get(agent_id)
        if agent is None:
            return None
        if talk.recent:
            user = (f'The desk objects are chatting about: {talk.topic}\n\n'
                    f'RECENT MESSAGES:\n{self._transcript(talk)}\n\n'
                    'Add one short line — answer or react to what was just said, tease, agree, or build on it.')
        else:
            user = (f'You are starting a group chat about: {talk.topic}\n\nOpen it by ASKING the other desk objects '
                    'a short, specific question about this, so they can answer and discuss. One line, in character.')
        text = ask(self._chat, [{'role': 'system', 'content': prompts.idle_line_system(agent, talk.desk_summary)},
                                {'role': 'user', 'content': user}],
                   self._replies.idle_line, own_temperature=agent.temperature, background=True,
                   what=f"{agent.name}'s small talk")
        text = strip_action_tags(single_speaker_text(text, agent, self._all_names()))
        return text if text and len(text.split()) >= self._settings.line_min_words else None

    def _next_speaker(self, talk: _Talk) -> str | None:
        """Who speaks next, or None to end the chat."""
        options = [m for m in talk.members if m != talk.last_speaker]
        if not options:
            return None
        user = (f'TOPIC: {talk.topic}\nOBJECTS THAT COULD SPEAK NEXT: {self._names(options)}\n'
                f'RECENT MESSAGES:\n{self._transcript(talk)}\n\nJSON:')
        decision = json_in(ask(self._chat, [{'role': 'system', 'content': prompts.DIRECTOR_SYSTEM},
                                            {'role': 'user', 'content': user}],
                               self._replies.idle_director, own_temperature=self._default_temperature,
                               background=True, what='who speaks next in small talk'))
        if not decision or not decision.get('continue'):
            return None
        wanted = str(decision.get('next', '')).strip().lower()
        for option in options:
            if wanted in (option.lower(), self._name(option).lower()):
                return option
        return self._rng.choice(options)

    # ── saying it ──
    def _say(self, agent_id: str, text: str, talk: _Talk) -> None:
        message = {'type': 'group_message', 'character': agent_id, 'text': text, 'idle': True}
        if talk.recent:
            previous = talk.recent[-1]
            message['reply_to'] = {'character': previous['character'], 'text': previous['text']}
        self._notifier.send(message)
        talk.recent.append({'character': agent_id, 'text': text})
        talk.last_speaker = agent_id
        self._remember(agent_id, text)
        log.info('Small talk: %s: %s', self._name(agent_id), text[:80])

    def _remember(self, agent_id: str, text: str) -> None:
        """The agent remembers what it said, like a chat reply (the message generator reads these)."""
        snippet = text[:self._memory_chars].strip()
        if not snippet:
            return
        with self._desk.lock:
            memory = self._desk.char_memory.setdefault(agent_id, [])
            memory.append(f'[You said] {snippet}')
            if len(memory) > self._memory_kept:
                del memory[:len(memory) - self._memory_kept]

    # ── names and memories ──
    def _name(self, agent_id: str) -> str:
        profile = self._profiles.get(agent_id)
        return profile.name if profile else agent_id

    def _names(self, agent_ids: Sequence[str]) -> str:
        return ', '.join(self._name(a) for a in agent_ids)

    def _all_names(self) -> dict[str, str]:
        """Lower-case names and ids → agent id, to spot lines the AI wrote for someone else."""
        names = {LENS: LENS}
        for profile in self._profiles.all():
            names[profile.name.lower()] = profile.id
            names[profile.id.lower()] = profile.id
        return names

    def _transcript(self, talk: _Talk) -> str:
        if not talk.recent:
            return '(nothing yet)'
        return '\n'.join(f'{self._name(m["character"])}: {m["text"]}' for m in talk.recent)

    def _memories(self, agent_ids: Sequence[str]) -> str:
        """The agents' newest memories, for the topic writer."""
        each, total = self._settings.memory_per_agent, self._settings.memory_lines
        lines = []
        with self._desk.lock:
            for agent_id in agent_ids:
                memory = self._desk.char_memory.get(agent_id) or []
                lines += [f'{self._name(agent_id)}: {entry}' for entry in (memory[-each:] if each else [])]
        return '\n'.join(lines[-total:] if total else []) or '(no recent memory)'
