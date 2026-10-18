"""
The chat: turns events into messages and answers the user.

It is a consumer of the event stream:
    agent_joined / agent_left  → "Cup joined the chat" (+ a hello)
    alert                      → an agent warns about what the camera saw
    habit                      → "Sip detected! That's 3 today."
and it answers messages you type (1-on-1 or in the group).

Messages reach the app over Server-Sent Events (/api/events): broadcast()
puts one in every open browser tab's queue.
"""

from __future__ import annotations

import queue
import random
from collections import deque
import threading
import time
import uuid

from ..events.stream import AGENT_JOINED, AGENT_LEFT
from . import prompts
from .context import (budget_history, build_live_context, response_violates_evidence, sanitize_chat_history,
                      single_speaker_text)
from .llm import looks_cut_off
from .widgets import action_instructions, parse_actions, strip_action_tags, tracker_prompt

HABIT_AREAS = {
    'hydration': 'Hydration', 'breaks': 'Movement breaks', 'screens': 'Screens and eye health',
    'light': 'Sunlight and evening light', 'phone_free': 'Phone-free focus', 'food': 'Healthy eating',
    'tasks': 'Task management & focus',
}


class MessageGenerator:
    def __init__(self, app):
        self.app = app
        self.llm = app.llm
        self._sse: list[queue.Queue] = []
        self.recent: deque = deque(maxlen=150)   # recent chat messages, for a page that opens later
        self._lock = threading.Lock()
        self._sent_messages: dict = {}       # alert key → last thing said about it (for follow-ups)
        self._greeted: dict = {}             # agent id → last hello
        s = app.settings
        self.greet = bool(s.agents.get('greet_on_join', True))
        self.announce = bool(s.agents.get('announce', True))
        self.greet_again_sec = float(s.agents.get('greet_again_after_min', 30)) * 60
        self.invent = bool(s.agents.get('invent_personality', True))
        self.memory_max = int(s.llm.get('memory_per_agent', 8))
        self.history_turns = int(s.llm.get('chat_history_turns', 6))

    # ── Server-Sent Events ──
    def broadcast(self, msg: dict) -> dict:
        msg.setdefault('id', uuid.uuid4().hex[:12])
        msg.setdefault('ts', time.time())
        if msg.get('character') and msg.get('text'):
            msg['text'] = strip_action_tags(msg['text'])
        with self._lock:
            if msg.get('type') in ('group_message', 'roster') and msg.get('text'):
                self.recent.append(msg)
            dead = []
            for q in self._sse:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._sse.remove(q)
        return msg

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=500)
        with self._lock:
            self._sse.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            if q in self._sse:
                self._sse.remove(q)

    # ── helpers ──
    def agent(self, pid):
        return self.app.profiles.get(pid)

    def _names(self) -> dict:
        out = {}
        for p in self.app.profiles.profiles.values():
            out[p.name.lower()] = p.id
            out[p.id.lower()] = p.id
        out['lens'] = 'lens'
        return out

    def habit_area(self, a) -> str:
        return ', '.join(HABIT_AREAS.get(h, h) for h in a.habits) or 'desk habits'

    def record_memory(self, pid: str, response_text: str, user_message: str = '') -> None:
        snippet = (response_text or '')[:120].strip()
        if not snippet:
            return
        entry = f'[You said] {snippet}'
        if user_message:
            entry = f'[User said "{(user_message or "")[:80].strip()}"] → [You replied] {snippet}'
        desk = self.app.desk
        with desk.lock:
            mem = desk.char_memory.setdefault(pid, [])
            mem.append(entry)
            if len(mem) > self.memory_max:
                desk.char_memory[pid] = mem[-self.memory_max:]

    # ════════════════════════════════════════════════════════════════
    #  Consumers
    # ════════════════════════════════════════════════════════════════
    def on_roster(self, ev: dict) -> None:
        pid = ev.get('agent')
        a = self.agent(pid)
        if not a:
            return
        joined = ev['type'] == AGENT_JOINED
        info = a.public()
        info['on_desk'] = joined
        self.broadcast({'type': 'roster', 'action': 'joined' if joined else 'left', 'agent': info,
                        'text': f'{a.name} {"joined" if joined else "left"} the chat', 'quiet': not self.announce})
        if not joined:
            return
        if a.auto and not a.learned and self.invent and self.llm.background_ok():
            text = self.llm.chat(prompts.invent_personality(a.object), max_tokens=90, temperature=0.9, background=True)
            if text and len(text.split()) >= 5:
                self.app.profiles.set_personality(pid, text)
        now = time.time()
        if self.greet and a.hello_text() and now - self._greeted.get(pid, 0) >= self.greet_again_sec:
            self._greeted[pid] = now
            self.broadcast({'type': 'group_message', 'character': pid, 'text': a.hello_text(), 'hello': True})

    def on_habit(self, ev: dict) -> None:
        """Things the habit tracker noticed (sip, break, food, phone-free streak)."""
        self.broadcast({'type': 'habit_update', 'habit': ev.get('habit'), 'action': ev.get('action')})
        text = ev.get('say')
        if not text or ev.get('quiet'):
            return
        from ..habits.reminders import speaker_for
        speaker = speaker_for((ev.get('habit'),), self.app.roster.member_ids(), self.app.profiles)
        self.broadcast({'type': 'group_message', 'character': speaker, 'text': text, 'habit': ev.get('habit')})

    def on_alert(self, ev: dict) -> None:
        msg = self.message_for_alert(ev)
        if msg:
            self.broadcast(msg)

    # ── alerts → messages ──
    def message_for_alert(self, ev: dict) -> dict | None:
        pid = ev.get('agent') or 'lens'
        snapshot = ev.get('snapshot')
        if ev.get('kind') != 'proximity' or ev.get('text'):
            # Alerts that already carry their own text (pet, clutter, lost item...) are sent as written.
            if not ev.get('text'):
                return None
            speaker = pid if self.agent(pid) else 'lens'
            msg = {'type': 'group_message', 'character': speaker, 'text': ev['text'], 'alert': ev.get('feature', '')}
            if snapshot:
                msg['snapshot'] = snapshot
            return msg
        a = self.agent(pid)
        if not a:
            return None
        follow_up = int(ev.get('follow_up', 0))
        key = f'proximity_{pid}_{ev.get("near", "")}'
        prev = self._sent_messages.get(key) if follow_up > 0 else None
        text = self._llm_alert(a, ev, prev) if self.llm.ready else None
        if not text:
            other = self.agent(ev.get('near', ''))
            if other:
                text = a.too_close_text(other.name)
            elif ev.get('rule_label'):
                text = f'Heads up: {ev["rule_label"].rstrip(".")}. Could you move me back a bit?'
            else:
                text = 'Something is getting too close. Could you check the desk?'
        msg = {'type': 'group_message', 'character': pid, 'text': text, 'alert': 'proximity'}
        if snapshot:
            msg['snapshot'] = snapshot
        if prev:
            msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
        self._sent_messages[key] = {'character': pid, 'text': text}
        return msg

    def describe_alert(self, a, ev: dict) -> str:
        other_id = ev.get('near', '')
        with self.app.desk.lock:
            objs = dict(self.app.desk.objects)
        self_label = (objs.get(a.id) or {}).get('class_name', 'unknown')
        other = self.agent(other_id)
        other_label = (objs.get(other_id) or {}).get('class_name', 'unknown')
        other_name = other.name if other else 'something'
        drinks = self.app.profiles.labels_for_habit('hydration') or {'cup', 'bottle', 'wine glass'}
        if other_label in drinks:
            drink_note = f'The other object is a drink class ({other_label}) — spill risk is real. '
        elif self_label in drinks:
            drink_note = f'You are a drink class ({self_label}). '
        else:
            drink_note = ('NEITHER object is a cup/bottle/wine_glass in the detector — do NOT claim liquid or cup. '
                          'Use the exact class names above (e.g. book, keyboard). ')
        risk = ev.get('rule_label') or self.app.profiles.relationship_risk(a.id, other_id) or \
            f'{self_label} and {other_label} are in the same area.'
        return (f'VISION GROUNDING: this chat message was triggered by geometry rules; detector classes are '
                f'"{self_label}" (you, {a.name}) and "{other_label}" (other: {other_name}). {drink_note}'
                f'Rule / risk: {risk}. '
                f'Reply using ONLY these class words for objects — not generic "cup" unless the other is a drink. '
                f'Use normal language: "check spacing" or "move it back", not catastrophic damage.')

    def _llm_alert(self, a, ev, prev):
        situation = self.describe_alert(a, ev)
        text = self.llm.chat([
            {'role': 'system', 'content': prompts.event_system(a)},
            {'role': 'user', 'content': prompts.event_user(a, situation, prev and prev['text'],
                                                           int(ev.get('follow_up', 0)))},
        ], temperature=0.55, background=True)
        if not text or looks_cut_off(text):
            return None
        text = single_speaker_text(text, a, self._names())
        if not text or response_violates_evidence(text, build_live_context(self.app), '', []):
            return None
        return text

    # ════════════════════════════════════════════════════════════════
    #  Habit reminders (protocols decide when; the agent says it)
    # ════════════════════════════════════════════════════════════════
    def phrase_nudge(self, pid: str, nudge) -> str | None:
        a = self.agent(pid)
        if not a or not self.llm.ready or not self.llm.background_ok():
            return None
        today = self.app.habits.today()
        with self.app.desk.lock:
            todo = self.app.desk.char_widgets.get(pid)
            signals = self.app.desk.habit_vision_brief
            vlmx = (self.app.desk.habit_vlm_brief or '').strip()
        block = tracker_prompt(a.tracker, today, todo)
        if vlmx:
            signals += f'; vision note: {vlmx}'
        system = prompts.habit_system(a, self.habit_area(a), block, signals or 'none', nudge.evidence, nudge.tip,
                                      short=self.llm.is_ollama)
        text = self.llm.chat([{'role': 'system', 'content': system},
                              {'role': 'user', 'content': 'Send a quick habit reminder right now.'}],
                             max_tokens=80, temperature=a.temperature, background=True)
        if text and not looks_cut_off(text):
            text = single_speaker_text(text, a, self._names())
            if text:
                self.record_memory(pid, text, 'habit reminder')
                return text
        return None

    # ════════════════════════════════════════════════════════════════
    #  Answering the user
    # ════════════════════════════════════════════════════════════════
    def generate_chat_response(self, pid: str, user_message: str, recent_messages=None):
        """Reply as agent `pid`, grounded in what the camera sees. Returns (text, actions)."""
        a = self.agent(pid)
        if not a or not self.llm.ready:
            return None, []
        context = build_live_context(self.app, user_message)
        mentioned = context['mentioned_objects']
        history = sanitize_chat_history(recent_messages)
        with self.app.desk.lock:
            todo = self.app.desk.char_widgets.get(pid)
            views = list(self.app.desk.widget_views.get(pid, []))
            memory_lines = list(self.app.desk.char_memory.get(pid, []))
        block = tracker_prompt(a.tracker, self.app.habits.today(), todo, views)
        recent_turns, older_recap = budget_history(history, self.history_turns, self.llm.is_ollama)

        # Long-term memory: only entries not already visible in the verbatim turns.
        memory_block = ''
        if memory_lines:
            seen_blob = ' '.join(str(t.get('text', '')).lower() for t in recent_turns)
            novel = [l for l in memory_lines if l[:40].lower() not in seen_blob]
            if self.llm.is_ollama:
                novel = novel[-3:]
            if novel:
                memory_block = ('\nWHAT YOU REMEMBER (older context, may not be shown above):\n'
                                + '\n'.join(f'- {l}' for l in novel) + '\n')
        recap_block = f'\nEARLIER IN THIS THREAD: {older_recap}\n' if older_recap else ''

        # Layer 1: stable identity + rules. Layer 2: live desk context. Layer 3: the conversation.
        identity = prompts.chat_identity(a, self.habit_area(a), action_instructions(a.tracker), self.llm.is_ollama)
        system = (identity + '\n\n---\nLIVE DESK STATUS (cameras; noisy, can be wrong):\n'
                  + context['summary'] + '\n' + block + memory_block + recap_block)
        messages = [{'role': 'system', 'content': system}]
        for turn in recent_turns:
            if turn['role'] == 'user':
                messages.append({'role': 'user', 'content': turn['text']})
            else:
                t, nm = turn['text'], (turn.get('name') or '').strip()
                if nm and nm != a.name:
                    # Other objects' lines in the third person, so the model doesn't take on their voice.
                    t = f'[{nm} said]: {t}'
                messages.append({'role': 'assistant', 'content': t})
        user_prompt = user_message
        if mentioned:
            user_prompt += '\n\n[Referenced objects — ' + '; '.join(f'@{m["name"]}: {m["status"]}' for m in mentioned) + ']'
        user_prompt += prompts.reanchor(a)
        messages.append({'role': 'user', 'content': user_prompt})

        text = self.llm.chat(messages, max_tokens=220, temperature=a.temperature)
        if not text:
            print(f'  [llm] empty/None ({pid}) — no message sent')
            return None, []
        print(f'  [llm] raw ({pid}): {text[:120]}')
        clean, actions = parse_actions(text, a.tracker)
        clean = single_speaker_text(clean, a, self._names())
        if not clean:
            return None, []
        self.record_memory(pid, clean, user_message)
        return clean, actions

    def no_reply_reason(self) -> str:
        """Why nobody answered, in plain words (shown in the chat)."""
        if not self.llm.ready:
            return ('The characters need an AI to chat. Add a key to .env (GEMINI_API_KEY is free; '
                    'or set OLLAMA_URL) and restart the server.')
        return self.llm.problem() or 'The AI sent back an empty reply. Try saying it again.'

    def random_member(self) -> str | None:
        members = self.app.roster.member_ids()
        return random.choice(members) if members else None
