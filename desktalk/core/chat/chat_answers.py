"""
Answering you: what /api/chat replies, 1-on-1 with an agent or in the group.

Each reply is grounded in the live desk, the agent's tracker panel, what the agent remembers and the
conversation so far. Action tags in the AI's reply ([LOG_SIP], [ADD_TODO:...]) are logged or handed
back to the app. When nobody can answer, the reply says why in plain words.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from desktalk.config import ChatSettings, LlmSettings
from desktalk.config.prompts import Prompts
from desktalk.core.chat import prompts
from desktalk.core.chat.agent_ai import AgentAI
from desktalk.core.chat.history import budget_history, sanitize_chat_history
from desktalk.core.chat.memory import ChatMemory, TrackerPanels
from desktalk.core.chat.names import group_speaker_order
from desktalk.core.chat.tracker_actions import action_instructions, apply_habit_actions, parse_actions, tracker_prompt
from desktalk.domain import AgentProfile, AIUnavailable
from desktalk.ports import CharacterMemory, ChatModel, Clock, HabitLog, HabitsView, Notifier, Profiles, RosterView

log = logging.getLogger(__name__)


class UnknownChat(ValueError):
    """answer() was asked to reply in a 1-on-1 chat with an agent that doesn't exist."""


class ChatAnswers:
    """`live_context(user_message)`: the desk right now for the AI. `desk`: the tracker panels are read from it.
    Settings: llm (chat_history_turns), chat, sunlight_default_min (habit_tracker), preview_chars (logging:
    the longest reply quoted in the log). `local`: the AI is a small model on this computer (Ollama), which
    gets shorter prompts and fewer earlier messages."""

    def __init__(self, ai: AgentAI, model: ChatModel, notifier: Notifier, roster: RosterView, profiles: Profiles,
                 habits: HabitsView, desk: CharacterMemory, memory: ChatMemory, panels: TrackerPanels, clock: Clock,
                 live_context: Callable[[str], Mapping[str, Any]], *, habit_log: HabitLog, words: Prompts,
                 llm: LlmSettings, chat: ChatSettings, sunlight_default_min: float, preview_chars: int, local: bool,
                 rng: random.Random):
        self._ai = ai
        self._model = model
        self._notifier = notifier
        self._roster = roster
        self._profiles = profiles
        self._habits = habits
        self._desk = desk
        self._memory = memory
        self._panels = panels
        self._clock = clock
        self._live_context = live_context
        self._habit_log = habit_log
        self._words = words
        self._llm = llm
        self._chat = chat
        self._sunlight_default_min = sunlight_default_min
        self._preview_chars = preview_chars
        self._local = local
        self._rng = rng

    def answer(self, chat_id: str, message: str, history: Any = None, widget: Any = None) -> dict[str, Any]:
        """The reply body for /api/chat (after the route has checked for an empty message and a lost-item
        question). `chat_id` is 'group' or an agent id; `history` is the earlier turns the app sent;
        `widget` the to-do list sent with a 1-on-1 message. Raises UnknownChat for an unknown agent.

        1-on-1:  {'messages': [{'character', 'text'}]}, plus 'actions' (what's left for the app) and
                 'habits_changed' when the AI used action tags; {'messages': [], 'error'} when nobody answers.
        Group:   {'messages': [<group_message as sent live, with id and ts; reply_to from the 2nd on>],
                 'streamed': True}, plus 'error' when nobody answered; {'messages': [], 'error'} when
                 nobody is on the desk."""
        turns = sanitize_chat_history(history, self._chat)
        if chat_id == 'group':
            return self._answer_group(message, turns)
        if not self._profiles.get(chat_id):
            raise UnknownChat(chat_id)
        self.save_widget(chat_id, widget)
        text, actions, reason = self._reply(chat_id, message, turns)
        if not text:
            return {'messages': [], 'error': reason}
        left = apply_habit_actions(self._habit_log, actions)
        out: dict[str, Any] = {'messages': [{'character': chat_id, 'text': text}]}
        if actions:
            out['actions'] = left
            out['habits_changed'] = len(left) != len(actions)
        return out

    def _answer_group(self, message: str, turns: list[dict]) -> dict[str, Any]:
        speakers = group_speaker_order(message, self._profiles.mention_words(), self._roster.member_ids(),
                                       [p.id for p in self._profiles.all()], self._rng)
        if not speakers:
            return {'messages': [], 'error': self._words.replies.nobody_here}
        rolling: list[dict] = list(turns)
        prev: dict[str, Any] | None = None
        sent: list[dict[str, Any]] = []
        reason = None
        for pid in speakers:
            text, actions, why = self._reply(pid, message, rolling)
            profile = self._profiles.get(pid)
            if not text or profile is None:
                reason = why
                continue
            apply_habit_actions(self._habit_log, actions)
            msg: dict[str, Any] = {'type': 'group_message', 'character': pid, 'text': text}
            if prev:
                msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
            # Shows up live right away; it's in the reply too (same id) in case the live connection dropped.
            msg = self._notifier.send(msg)
            rolling.append({'role': 'assistant', 'name': profile.name, 'text': text})
            prev = msg
            sent.append(msg)
        out: dict[str, Any] = {'messages': sent, 'streamed': True}
        if not sent:
            out['error'] = reason or self.no_reply_reason()
        return out

    def _reply(self, pid: str, user_message: str,
               history: Sequence[Mapping[str, Any]]) -> tuple[str | None, list[dict], str]:
        """(text, actions, why not) for agent `pid`'s reply, grounded in what the camera sees. `history` is
        already cleaned (history.sanitize_chat_history)."""
        a = self._profiles.get(pid)
        if not a or not self._model.ready:
            return None, [], self.no_reply_reason()
        try:
            text = self._ai.call(self._reply_messages(a, user_message, list(history)), 'chat', a, background=False)
        except AIUnavailable as err:
            log.info('No reply from %s: %s', pid, err.reason)
            return None, [], err.reason
        log.debug('AI reply for %s: %s', pid, text[:self._preview_chars])
        clean, actions = parse_actions(text, a.tracker, sunlight_default_min=self._sunlight_default_min)
        clean = self._ai.one_speaker(clean, a)
        if not clean:
            return None, [], self._words.replies.empty_reply
        self._memory.remember(pid, clean, user_message)
        return clean, actions, ''

    def _reply_messages(self, a: AgentProfile, user_message: str, history: list) -> list[dict[str, Any]]:
        """Layer 1: who the agent is and its rules. Layer 2: the live desk. Layer 3: the conversation."""
        context = self._live_context(user_message)
        now = self._clock.now()
        todo = self._desk.widget(a.id)
        views = self._desk.widget_views_of(a.id)
        memory_lines = self._memory.recent(a.id)
        block = tracker_prompt(a.tracker, self._habits.today(now), todo, views, now, self._chat)
        recent_turns, older_recap = budget_history(history, self._llm.chat_history_turns, self._chat, self._local)

        # Long-term memory: only what isn't already in the turns sent word for word.
        memory_block = ''
        if memory_lines:
            seen_blob = ' '.join(str(t.get('text', '')).lower() for t in recent_turns)
            novel = [m for m in memory_lines if m[:self._chat.memory_match_chars].lower() not in seen_blob]
            if self._local:
                keep = self._chat.ollama_memory_lines
                novel = novel[-keep:] if keep else []
            if novel:
                memory_block = ('\nWHAT YOU REMEMBER (older context, may not be shown above):\n'
                                + '\n'.join(f'- {m}' for m in novel) + '\n')
        recap_block = f'\nEARLIER IN THIS THREAD: {older_recap}\n' if older_recap else ''

        w = self._words
        identity = prompts.chat_identity(w.chat, a, prompts.habit_area(w.habit_areas, a),
                                         action_instructions(a.tracker), self._local)
        system = (identity + '\n\n---\nLIVE DESK STATUS (cameras; noisy, can be wrong):\n'
                  + context['summary'] + '\n' + block + memory_block + recap_block)
        messages: list[dict[str, Any]] = [{'role': 'system', 'content': system}]
        for turn in recent_turns:
            if turn['role'] == 'user':
                messages.append({'role': 'user', 'content': turn['text']})
            else:
                text, name = turn['text'], (turn.get('name') or '').strip()
                if name and name != a.name:
                    # Other objects' lines in the third person, so the AI doesn't take on their voice.
                    text = f'[{name} said]: {text}'
                messages.append({'role': 'assistant', 'content': text})
        user_prompt = user_message
        mentioned = context['mentioned_objects']
        if mentioned:
            user_prompt += ('\n\n[Referenced objects — '
                            + '; '.join(f'@{m["name"]}: {m["status"]}' for m in mentioned) + ']')
        user_prompt += prompts.reanchor(w.chat, a)
        messages.append({'role': 'user', 'content': user_prompt})
        return messages

    def no_reply_reason(self) -> str:
        """Why nobody answered, in plain words (shown in the chat)."""
        if not self._model.ready:
            return self._words.replies.needs_an_ai
        return self._model.problem() or self._words.replies.empty_reply

    # ── the tracker panel (/api/chat_todos, /api/chat_todos_sync, /api/widget_event) ──
    def save_widget(self, pid: str, raw: Any) -> bool:
        """Keep the to-do list the app sent for an agent's panel. False if the agent or the list isn't valid."""
        return bool(self._profiles.get(pid)) and self._panels.save(pid, raw)

    def note_panel_event(self, pid: str, action: Any) -> bool:
        """The user opened (or used) an agent's tracker panel; the agent may mention it.
        False (nothing kept) for an agent that doesn't exist. The action is cut to chat.panel_action_max_chars."""
        if not self._profiles.get(pid):
            return False
        self._panels.note_view(pid, action, self._clock.now())
        return True
