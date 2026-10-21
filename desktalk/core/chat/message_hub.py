"""
MessageHub: sends chat messages to every open app page (the live chat, over Server-Sent Events).

Each open page subscribes and gets its own bounded queue; the web layer's SSE route reads it.
A page that stops reading (its queue fills up) is dropped, so it can never hold up the others.
Recent chat lines and join / leave notes are kept for a page that opens later.

For the web layer: the queue holds message dicts, and at most one CLOSED after them. CLOSED (compare with
`is`) means the hub dropped this page and nothing more will come: the SSE route must end its response
then, so the browser's EventSource connects again (and subscribes afresh). For example:

    msg = q.get(timeout=keepalive_sec)
    if msg is CLOSED:
        return                       # ends the stream; the browser reconnects after its retry wait
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from collections import deque
from collections.abc import Mapping
from typing import Any

from desktalk.core.chat.tracker_actions import strip_action_tags
from desktalk.ports import Clock

log = logging.getLogger(__name__)

KEPT_TYPES = ('group_message', 'roster')


class _Closed:
    def __repr__(self) -> str:
        return 'CLOSED'


CLOSED = _Closed()      # the last thing in a dropped page's queue (see the top of this file)


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
                # One place in each queue is kept for CLOSED; only the hub puts things in, so qsize() is safe here.
                if q.qsize() >= self._queue_size:
                    stuck.append(q)
                else:
                    q.put_nowait(msg)
            for q in stuck:
                self._queues.remove(q)
                q.put_nowait(CLOSED)
        if stuck:
            log.info('%d live chat page(s) stopped reading and were dropped; the browser connects again by itself',
                     len(stuck))
        return msg

    def subscribe(self) -> queue.Queue:
        """A queue that gets every message from now on (one per open page), and CLOSED if the page is dropped."""
        q: queue.Queue = queue.Queue(maxsize=self._queue_size + 1)      # + 1: room for CLOSED
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
