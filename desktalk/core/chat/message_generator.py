"""
The chat: turns events into messages, puts habit reminders into words, and answers you.

It listens on the event bus:
    AgentJoined / AgentLeft  → "Cup joined the chat" (+ a hello, and an AI personality for a new kind of object)
    Alert                    → an agent warns about what the camera saw
    HabitEvent               → "Sip detected! That's 3 today."
and answer() replies to what you type, 1-on-1 or in the group (/api/chat).

When the AI is unavailable the characters still speak: alerts fall back to the agent's own
too_close line, and reminders to the nudge's ready-made text. Your own messages get a plain
reason instead of a reply.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.config import AgentsSettings, ChatSettings, LlmSettings
from desktalk.core.agents.speakers import LENS, speaker_for
from desktalk.core.chat import prompts
from desktalk.core.chat.context import (
    budget_history,
    build_live_context,
    group_speaker_order,
    looks_cut_off,
    mention_words,
    response_violates_evidence,
    sanitize_chat_history,
    single_speaker_text,
    speaker_names,
)
from desktalk.core.chat.tracker_actions import (
    action_instructions,
    apply_habit_actions,
    normalize_todo,
    parse_actions,
    tracker_prompt,
)
from desktalk.desk_state import DeskState
from desktalk.domain import AgentJoined, AgentLeft, AgentProfile, AIUnavailable, Alert, HabitEvent, Nudge
from desktalk.ports import (
    ChatModel,
    Clock,
    DeskStatus,
    EventBus,
    HabitLog,
    HabitsView,
    Notifier,
    Profiles,
    RosterView,
)

log = logging.getLogger(__name__)

# Labels that count as drinks when no agent speaks for hydration.
DRINK_LABELS = ('cup', 'bottle', 'wine glass')
NEEDS_AN_AI = ('The characters need an AI to chat. Add a key to .env (GEMINI_API_KEY is free; '
               'or set OLLAMA_URL) and restart the server.')
EMPTY_REPLY = 'The AI sent back an empty reply. Try saying it again.'
NOBODY_HERE = ('Nobody is on the desk yet. Put something in front of the camera, '
               'or @mention someone who was here earlier.')


class UnknownChat(ValueError):
    """answer() was asked to reply in a 1-on-1 chat with an agent that doesn't exist."""


class MessageGenerator:
    """The ChatVoice (desktalk/ports.py), and the chat's voice for events and the user's messages.

    Settings: llm (reply sizes, memory_per_agent, chat_history_turns), chat, agents (announce,
    greet_on_join, greet_again_after_min, invent_personality), sunlight_default_min (habit_tracker),
    reply_max_chars (longest AI reply kept). local_model: the AI is a small model on this computer
    (Ollama), which gets shorter prompts and fewer earlier messages."""

    def __init__(self, model: ChatModel, notifier: Notifier, roster: RosterView, profiles: Profiles,
                 habits: HabitsView, desk: DeskState, clock: Clock, *, status: DeskStatus, habit_log: HabitLog,
                 llm: LlmSettings, chat: ChatSettings, agents: AgentsSettings, sunlight_default_min: float,
                 reply_max_chars: int, local_model: bool = False, rng: random.Random | None = None):
        self._model = model
        self._notifier = notifier
        self._roster = roster
        self._profiles = profiles
        self._habits = habits
        self._desk = desk
        self._clock = clock
        self._status = status
        self._habit_log = habit_log
        self._llm = llm
        self._chat = chat
        self._agents = agents
        self._sunlight_default_min = sunlight_default_min
        self._reply_max_chars = reply_max_chars
        self._local = local_model
        self._rng = rng or random.Random()
        self._said_about: dict[str, dict[str, str]] = {}    # alert key → last thing said about it (follow-ups)
        self._greeted: dict[str, float] = {}                # agent id → last hello

    def subscribe_to(self, bus: EventBus) -> None:
        bus.subscribe('chat:roster', self.on_roster, types=(AgentJoined, AgentLeft))
        bus.subscribe('chat:alerts', self.on_alert, types=(Alert,))
        bus.subscribe('chat:habits', self.on_habit, types=(HabitEvent,))

    # ── helpers ──
    def speaker_names(self) -> dict[str, str]:
        """Lower-case names and ids → agent id (Lens included), for keeping AI replies to one speaker."""
        return speaker_names(self._profiles.all())

    def _call_ai(self, messages: list[dict[str, Any]], reply: str, agent: AgentProfile, background: bool) -> str:
        """The AI's reply; raises AIUnavailable. `reply` names the llm.replies size to use
        (no temperature there = the agent's own)."""
        size = getattr(self._llm.replies, reply)
        temperature = agent.temperature if size.temperature is None else size.temperature
        return self._model.chat(messages, max_tokens=size.max_tokens, temperature=temperature, background=background)

    def _ask(self, messages: list[dict[str, Any]], reply: str, agent: AgentProfile, background: bool) -> str | None:
        """The AI's reply, or None when it's unavailable."""
        try:
            return self._call_ai(messages, reply, agent, background)
        except AIUnavailable as err:
            log.info('No AI %s line for %s: %s', reply, agent.id, err.reason)
            return None

    def _one_speaker(self, text: str, agent: AgentProfile) -> str:
        return single_speaker_text(text, agent, self.speaker_names(), self._reply_max_chars)

    def record_memory(self, pid: str, response_text: str, user_message: str = '') -> None:
        """Remember what an agent said (and to what), for later replies."""
        snippet = (response_text or '')[:self._chat.memory_reply_chars].strip()
        if not snippet:
            return
        entry = f'[You said] {snippet}'
        if user_message:
            entry = (f'[User said "{(user_message or "")[:self._chat.memory_user_chars].strip()}"] '
                     f'→ [You replied] {snippet}')
        with self._desk.lock:
            memory = self._desk.char_memory.setdefault(pid, [])
            memory.append(entry)
            keep = self._llm.memory_per_agent
            self._desk.char_memory[pid] = memory[-keep:] if keep else []

    def live_context(self, user_message: str = '') -> dict[str, Any]:
        """Real-time desk facts for the AI."""
        now = self._clock.now()
        return build_live_context(self._desk, profiles=self._profiles, members=self._roster.member_ids(),
                                  habits_today=self._habits.today(now), now=now,
                                  camera_on=self._status.camera_connected(), chat=self._chat,
                                  user_message=user_message)

    # ════════════════════════════════════════════════════════════════
    #  Events
    # ════════════════════════════════════════════════════════════════
    def on_roster(self, ev: AgentJoined | AgentLeft) -> None:
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
            text = self._ask(prompts.invent_personality(a.object), 'personality', a, background=True)
            if text and len(text.split()) >= self._chat.personality_min_words:
                self._profiles.set_personality(a.id, text)
        now = self._clock.now()
        hello = a.hello_text()
        if (self._agents.greet_on_join and hello
                and now - self._greeted.get(a.id, float('-inf')) >= self._agents.greet_again_after_min * 60):
            self._greeted[a.id] = now
            self._notifier.send({'type': 'group_message', 'character': a.id, 'text': hello, 'hello': True})

    def on_habit(self, ev: HabitEvent) -> None:
        """Things the habit tracker noticed (a sip, a break, food, a phone-free streak)."""
        self._notifier.send({'type': 'habit_update', 'habit': ev.habit, 'action': ev.action})
        if not ev.say or ev.quiet:
            return
        speaker = speaker_for((ev.habit,), self._roster.member_ids(), self._profiles)
        self._notifier.send({'type': 'group_message', 'character': speaker, 'text': ev.say, 'habit': ev.habit})

    def on_alert(self, ev: Alert) -> None:
        msg = self.message_for_alert(ev)
        if msg:
            self._notifier.send(msg)

    def message_for_alert(self, ev: Alert) -> dict[str, Any] | None:
        d = ev.to_dict()
        pid = d.get('agent') or LENS
        snapshot = d.get('snapshot')
        if d.get('kind') != 'proximity' or d.get('text'):
            # Alerts that carry their own text (pet, clutter, lost item ...) are sent as written.
            if not d.get('text'):
                return None
            speaker = pid if self._profiles.get(pid) else LENS
            msg = {'type': 'group_message', 'character': speaker, 'text': d['text'], 'alert': d.get('feature', '')}
            if snapshot:
                msg['snapshot'] = snapshot
            return msg
        a = self._profiles.get(pid)
        if not a:
            return None
        follow_up = int(d.get('follow_up') or 0)
        near = d.get('near') or ''
        key = f'proximity_{pid}_{near}'
        prev = self._said_about.get(key) if follow_up > 0 else None
        text = self._ai_alert(a, d, prev, follow_up)
        if not text:
            other = self._profiles.get(near)
            if other:
                text = a.too_close_text(other.name)
            elif d.get('rule_label'):
                text = f'Heads up: {d["rule_label"].rstrip(".")}. Could you move me back a bit?'
            else:
                text = 'Something is getting too close. Could you check the desk?'
        msg = {'type': 'group_message', 'character': pid, 'text': text, 'alert': 'proximity'}
        if snapshot:
            msg['snapshot'] = snapshot
        if prev:
            msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
        self._said_about[key] = {'character': pid, 'text': text}
        return msg

    def describe_alert(self, a: AgentProfile, d: Mapping[str, Any]) -> str:
        """The situation, in the detector's own words, so the AI doesn't invent spills or damage."""
        other_id = d.get('near') or ''
        with self._desk.lock:
            objects = dict(self._desk.objects)
        self_label = (objects.get(a.id) or {}).get('class_name', 'unknown')
        other = self._profiles.get(other_id)
        other_label = (objects.get(other_id) or {}).get('class_name', 'unknown')
        other_name = other.name if other else 'something'
        drinks = self._profiles.labels_for_habit('hydration') or set(DRINK_LABELS)
        if other_label in drinks:
            drink_note = f'The other object is a drink class ({other_label}) — spill risk is real. '
        elif self_label in drinks:
            drink_note = f'You are a drink class ({self_label}). '
        else:
            drink_note = ('NEITHER object is a cup/bottle/wine_glass in the detector — do NOT claim liquid or cup. '
                          'Use the exact class names above (e.g. book, keyboard). ')
        risk = d.get('rule_label') or self._profiles.relationship_risk(a.id, other_id) or \
            f'{self_label} and {other_label} are in the same area.'
        return (f'VISION GROUNDING: this chat message was triggered by geometry rules; detector classes are '
                f'"{self_label}" (you, {a.name}) and "{other_label}" (other: {other_name}). {drink_note}'
                f'Rule / risk: {risk}. '
                f'Reply using ONLY these class words for objects — not generic "cup" unless the other is a drink. '
                f'Use normal language: "check spacing" or "move it back", not catastrophic damage.')

    def _ai_alert(self, a: AgentProfile, d: Mapping[str, Any], prev: Mapping[str, str] | None,
                  follow_up: int) -> str | None:
        situation = self.describe_alert(a, d)
        text = self._ask([{'role': 'system', 'content': prompts.event_system(a)},
                          {'role': 'user', 'content': prompts.event_user(a, situation, prev and prev['text'],
                                                                         follow_up)}],
                         'alert', a, background=True)
        if not text or looks_cut_off(text, self._chat.min_reply_words):
            return None
        text = self._one_speaker(text, a)
        if not text or response_violates_evidence(text, self.live_context(), '', []):
            return None
        return text

    # ════════════════════════════════════════════════════════════════
    #  Habit reminders (the Protocols rules decide when; the agent says it)
    # ════════════════════════════════════════════════════════════════
    def phrase_nudge(self, agent_id: str, nudge: Nudge) -> str | None:
        """The agent's words for a habit reminder: the AI's, or the nudge's own text when the AI can't help."""
        a = self._profiles.get(agent_id)
        if a:
            text = self._ai_nudge(a, nudge)
            if text:
                self.record_memory(agent_id, text, 'habit reminder')
                return text
        return nudge.text or None

    def _ai_nudge(self, a: AgentProfile, nudge: Nudge) -> str | None:
        now = self._clock.now()
        with self._desk.lock:
            todo = self._desk.char_widgets.get(a.id)
            vision_note = (self._desk.vision_habit_brief or '').strip()
        block = tracker_prompt(a.tracker, self._habits.today(now), todo, [], now, self._chat)
        signals = f'; vision note: {vision_note}' if vision_note else ''
        system = prompts.habit_system(a, prompts.habit_area(a), block, signals or 'none', nudge.evidence, nudge.tip,
                                      short=self._local)
        text = self._ask([{'role': 'system', 'content': system},
                          {'role': 'user', 'content': 'Send a quick habit reminder right now.'}],
                         'nudge', a, background=True)
        if not text or looks_cut_off(text, self._chat.min_reply_words):
            return None
        return self._one_speaker(text, a) or None

    # ════════════════════════════════════════════════════════════════
    #  Answering the user
    # ════════════════════════════════════════════════════════════════
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
        profiles = self._profiles.all()
        speakers = group_speaker_order(message, mention_words(profiles), self._roster.member_ids(),
                                       [p.id for p in profiles], self._rng)
        if not speakers:
            return {'messages': [], 'error': NOBODY_HERE}
        rolling, prev, sent, reason = list(turns), None, [], None
        for pid in speakers:
            text, actions, why = self._reply(pid, message, rolling)
            if not text:
                reason = why
                continue
            apply_habit_actions(self._habit_log, actions)
            msg: dict[str, Any] = {'type': 'group_message', 'character': pid, 'text': text}
            if prev:
                msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
            # Shows up live right away; it's in the reply too (same id) in case the live connection dropped.
            msg = self._notifier.send(msg)
            rolling.append({'role': 'assistant', 'name': self._profiles.get(pid).name, 'text': text})
            prev = msg
            sent.append(msg)
        out: dict[str, Any] = {'messages': sent, 'streamed': True}
        if not sent:
            out['error'] = reason or self.no_reply_reason()
        return out

    def _reply(self, pid: str, user_message: str,
               history: Sequence[Mapping[str, Any]]) -> tuple[str | None, list[dict], str]:
        """(text, actions, why not) for agent `pid`'s reply, grounded in what the camera sees. `history` is
        already cleaned (context.sanitize_chat_history)."""
        a = self._profiles.get(pid)
        if not a or not self._model.ready:
            return None, [], self.no_reply_reason()
        try:
            text = self._call_ai(self._reply_messages(a, user_message, list(history)), 'chat', a, background=False)
        except AIUnavailable as err:
            log.info('No reply from %s: %s', pid, err.reason)
            return None, [], err.reason
        log.debug('AI reply for %s: %s', pid, text[:120])
        clean, actions = parse_actions(text, a.tracker, sunlight_default_min=self._sunlight_default_min)
        clean = self._one_speaker(clean, a)
        if not clean:
            return None, [], EMPTY_REPLY
        self.record_memory(pid, clean, user_message)
        return clean, actions, ''

    def _reply_messages(self, a: AgentProfile, user_message: str, history: list) -> list[dict[str, Any]]:
        """Layer 1: who the agent is and its rules. Layer 2: the live desk. Layer 3: the conversation."""
        context = self.live_context(user_message)
        now = self._clock.now()
        with self._desk.lock:
            todo = self._desk.char_widgets.get(a.id)
            views = list(self._desk.widget_views.get(a.id, []))
            memory_lines = list(self._desk.char_memory.get(a.id, []))
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

        identity = prompts.chat_identity(a, prompts.habit_area(a), action_instructions(a.tracker), self._local)
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
        user_prompt += prompts.reanchor(a)
        messages.append({'role': 'user', 'content': user_prompt})
        return messages

    def no_reply_reason(self) -> str:
        """Why nobody answered, in plain words (shown in the chat)."""
        if not self._model.ready:
            return NEEDS_AN_AI
        return self._model.problem() or EMPTY_REPLY

    # ── the tracker panel (/api/chat_todos, /api/chat_todos_sync, /api/widget_event) ──
    def save_widget(self, pid: str, raw: Any) -> bool:
        """Keep the to-do list the app sent for an agent's panel. False if the agent or the list isn't valid."""
        if not self._profiles.get(pid):
            return False
        widget = normalize_todo(raw, self._chat)
        if not widget:
            return False
        with self._desk.lock:
            self._desk.char_widgets[pid] = widget
        return True

    def note_panel_event(self, pid: str, action: Any) -> bool:
        """The user opened (or used) an agent's tracker panel; the agent may mention it.
        False (nothing kept) for an agent that doesn't exist. The action is cut to chat.panel_action_max_chars."""
        if not self._profiles.get(pid):
            return False
        action = str(action)[:self._chat.panel_action_max_chars]
        with self._desk.lock:
            views = self._desk.widget_views.setdefault(pid, [])
            views.append({'ts': self._clock.now(), 'action': action})
            self._desk.widget_views[pid] = views[-self._chat.panel_views_kept:]
        return True
