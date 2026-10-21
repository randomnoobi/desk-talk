"""
MessageHub: sends chat messages to every open app page (the live chat, over Server-Sent Events).

Each open page subscribes and gets its own bounded queue; the web layer's SSE route reads it.
A page that stops reading (its queue fills up) is dropped, so it can never hold up the others.
Recent chat lines and join / leave notes are kept for a page that opens later.
"""

from __future__ import annotations

import queue
import threading
import uuid
from collections import deque
from collections.abc import Mapping
from typing import Any

from desktalk.core.chat.tracker_actions import strip_action_tags
from desktalk.ports import Clock

KEPT_TYPES = ('group_message', 'roster')


class MessageHub:
    """The Notifier (desktalk/ports.py). recent_kept: settings → chat.recent_kept;
    queue_size: settings → web.live_queue_size."""

    def __init__(self, clock: Clock, *, recent_kept: int, queue_size: int):
        self._clock = clock
        self._queue_size = queue_size
        self._lock = threading.Lock()
        self._queues: list[queue.Queue] = []
        self._recent: deque[dict[str, Any]] = deque(maxlen=recent_kept)

    def send(self, message: Mapping[str, Any]) -> dict[str, Any]:
        """Send to every open page. Returns the message as sent (with its id and ts filled in)."""
        msg = dict(message)
        msg.setdefault('id', uuid.uuid4().hex[:12])
        msg.setdefault('ts', self._clock.now())
        if msg.get('character') and msg.get('text'):
            msg['text'] = strip_action_tags(msg['text'])     # a tag the AI copied must never show
        with self._lock:
            if msg.get('type') in KEPT_TYPES and msg.get('text'):
                self._recent.append(msg)
            stuck = []
            for q in self._queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    stuck.append(q)
            for q in stuck:
                self._queues.remove(q)
        return msg

    def subscribe(self) -> queue.Queue:
        """A queue that gets every message from now on (one per open page)."""
        q: queue.Queue = queue.Queue(maxsize=self._queue_size)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def recent(self) -> list[dict[str, Any]]:
        """Recent chat lines and join / leave notes, oldest first (for /api/messages/recent)."""
        with self._lock:
            return list(self._recent)
