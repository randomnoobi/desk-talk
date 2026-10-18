"""
Idle chat: the agents on the desk sometimes chat with each other.

Director-orchestrated small talk. Per round:
  1. list the agents in the chat (only objects the camera can see take part)
  2. pick a random one to start
  3. the AI writes ONE small-talk topic from the desk state + recent memory
  4. the starter asks the others a question about it
  5. a "director" AI decides whether the chat continues and who speaks next;
     that agent adds one line — until the director ends it or the turn cap is hit

Each line is sent the moment it's ready. Timing: config/settings.yaml → idle_chat.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time

from . import prompts
from .context import build_live_context, single_speaker_text
from .widgets import strip_action_tags


class IdleChatThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name='IdleChat')
        self.app = app
        s = app.settings.idle_chat
        self.min_sec = int(s.get('min_sec', 720))
        self.max_sec = int(s.get('max_sec', 1500))
        self.start_delay = float(s.get('start_delay_sec', 15))
        self.max_turns = int(s.get('max_turns', 3))
        self.quiet_sec = float(s.get('quiet_after_typing_sec', 10))
        self.gen = app.generator
        self.llm = app.llm

    # ── helpers ──
    def _agent_name(self, pid):
        p = self.app.profiles.get(pid)
        return p.name if p else pid

    def _names(self, ids):
        return ', '.join(self._agent_name(c) for c in ids)

    def _transcript(self, recent):
        if not recent:
            return '(nothing yet)'
        return '\n'.join(f'{self._agent_name(m["character"])}: {m["text"]}' for m in recent)

    def _memory_blob(self, ids):
        lines = []
        with self.app.desk.lock:
            for c in ids:
                for entry in (self.app.desk.char_memory.get(c) or [])[-2:]:
                    lines.append(f'{self._agent_name(c)}: {entry}')
        return '\n'.join(lines[-6:]) or '(no recent memory)'

    # ── AI steps ──
    def generate_topic(self, on_desk, desk_summary):
        user = (f'DESK STATUS:\n{desk_summary}\n\nOBJECTS PRESENT: {self._names(on_desk)}\n'
                f'RECENT MEMORY:\n{self._memory_blob(on_desk)}\n\nTopic:')
        topic = self.llm.chat([{'role': 'system', 'content': prompts.TOPIC_SYSTEM}, {'role': 'user', 'content': user}],
                              max_tokens=200, temperature=0.9, background=True)
        if not topic:
            return None
        topic = topic.strip().strip('"\'').splitlines()[0].strip()
        return topic if len(topic.split()) >= 2 else None

    def character_line(self, pid, topic, recent, desk_summary):
        a = self.app.profiles.get(pid)
        if recent:
            user = (f'The desk objects are chatting about: {topic}\n\nRECENT MESSAGES:\n{self._transcript(recent)}\n\n'
                    'Add one short line — answer or react to what was just said, tease, agree, or build on it.')
        else:
            user = (f'You are starting a group chat about: {topic}\n\nOpen it by ASKING the other desk objects a short, '
                    'specific question about this, so they can answer and discuss. One line, in character.')
        text = self.llm.chat([{'role': 'system', 'content': prompts.idle_line_system(a, desk_summary)},
                              {'role': 'user', 'content': user}],
                             max_tokens=200, temperature=a.temperature, background=True)
        if not text:
            return None
        text = strip_action_tags(single_speaker_text(text, a, self.gen._names()))
        return text if text and len(text.split()) >= 3 else None

    def direct(self, topic, recent, on_desk, last_speaker):
        """Returns the next agent id, or None to end the conversation."""
        options = [c for c in on_desk if c != last_speaker]
        if not options:
            return None
        user = (f'TOPIC: {topic}\nOBJECTS THAT COULD SPEAK NEXT: {self._names(options)}\n'
                f'RECENT MESSAGES:\n{self._transcript(recent)}\n\nJSON:')
        raw = self.llm.chat([{'role': 'system', 'content': prompts.DIRECTOR_SYSTEM}, {'role': 'user', 'content': user}],
                            max_tokens=200, temperature=0.4, background=True)
        match = re.search(r'\{.*\}', raw or '', re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return None
        if not data.get('continue'):
            return None
        want = str(data.get('next', '')).strip().lower()
        for c in options:
            if want in (c.lower(), self._agent_name(c).lower()):
                return c
        return random.choice(options)

    def say(self, pid, text, recent):
        msg = {'type': 'group_message', 'character': pid, 'text': text, 'idle': True}
        if recent:
            prev = recent[-1]
            msg['reply_to'] = {'character': prev['character'], 'text': prev['text']}
        self.gen.broadcast(msg)
        recent.append({'character': pid, 'text': text})
        self.gen.record_memory(pid, text)
        print(f'  [idle-chat] {self._agent_name(pid)}: {text[:80]}')

    def run_conversation(self, on_desk):
        summary = build_live_context(self.app)['summary']
        topic = self.generate_topic(on_desk, summary)
        if not topic:
            print('  [idle-chat] no topic (AI returned nothing) — skipping this round')
            return 0
        initiator = random.choice(on_desk)
        recent = []
        line = self.character_line(initiator, topic, recent, summary)
        if not line:
            return 0
        self.say(initiator, line, recent)
        last = initiator
        for _ in range(self.max_turns):
            if time.time() - self.app.desk.last_user_activity < self.quiet_sec:
                print('  [idle-chat] user became active — ending conversation early')
                break
            nxt = self.direct(topic, recent, on_desk, last)
            if not nxt:
                break
            time.sleep(3 + random.random() * 3)
            line = self.character_line(nxt, topic, recent, summary)
            if not line:
                break
            self.say(nxt, line, recent)
            last = nxt
        return len(recent)

    def run(self):
        time.sleep(self.start_delay)
        while True:
            time.sleep(random.randint(self.min_sec, self.max_sec))
            # Hold off while the user is typing.
            wait = self.quiet_sec - (time.time() - self.app.desk.last_user_activity)
            while wait > 0:
                time.sleep(wait)
                wait = self.quiet_sec - (time.time() - self.app.desk.last_user_activity)
            now = time.time()
            if not self.app.reminders_ready(now) or not self.app.user_active(now):
                continue
            if not self.llm.background_ok():
                continue
            members = self.app.roster.member_ids()
            if len(members) < 2:
                continue
            try:
                self.run_conversation(members)
            except Exception as exc:
                print(f'  [idle-chat] Error: {exc!r}')
