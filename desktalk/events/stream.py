"""
The event stream: producer → stream → consumers.

Producers publish events (a picture was analysed, an agent joined, a sip was
logged...). Each consumer subscribes with its own queue and thread, so a slow
consumer (say, the AI) never holds up the camera. This is the "decoupling"
box on the whiteboard; it plays the part Kafka or RabbitMQ would in a bigger
system, but runs inside this one program.

    stream = EventStream()
    stream.subscribe('event-log', log.write)                    # every event
    stream.subscribe('roster', roster.on_detection, types={'detection'})
    stream.publish('detection', objects=[...], frame_id='pi-000123')

Every event is a plain dict: {"id", "ts", "type", ...data}.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
import uuid
from typing import Callable, Iterable

# Event types used in the app (any other string works too).
DETECTION = 'detection'          # one analysed picture: what was seen and where (3 per second)
AGENT_JOINED = 'agent_joined'    # "Cup joined the chat"
AGENT_LEFT = 'agent_left'        # "Cup left the chat"
ALERT = 'alert'                  # something risky on the desk (cup near laptop, near the edge, pet)
HABIT = 'habit'                  # a habit happened (sip, break, sunlight, phone-free block)
NUDGE = 'nudge'                  # a reminder was sent
CAMERA = 'camera'                # Lens noticed a camera problem / fix
COMPACTION = 'compaction'        # the event log turned pictures into intervals

_seq = itertools.count(1)


def make_event(event_type: str, ts: float | None = None, **data) -> dict:
    return {'id': f'{next(_seq):x}-{uuid.uuid4().hex[:6]}', 'ts': ts if ts is not None else time.time(),
            'type': event_type, **data}


class Subscription:
    def __init__(self, name: str, handler: Callable[[dict], None], types: set | None, maxsize: int):
        self.name = name
        self.handler = handler
        self.types = set(types) if types else None
        self.queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        self.handled = 0
        self.errors = 0
        self.thread: threading.Thread | None = None

    def wants(self, event_type: str) -> bool:
        return self.types is None or event_type in self.types


class EventStream:
    """In-process publish / subscribe.

    synchronous=True runs handlers right inside publish(), in the order they
    subscribed. Tests use it so results don't depend on thread timing."""

    def __init__(self, synchronous: bool = False, log_errors: bool = True):
        self.synchronous = synchronous
        self.log_errors = log_errors
        self._subs: list[Subscription] = []
        self._lock = threading.Lock()
        self._closed = False
        self.published = 0

    # ---- consumers ----
    def subscribe(self, name: str, handler: Callable[[dict], None], types: Iterable[str] | None = None,
                  maxsize: int = 2000) -> Subscription:
        sub = Subscription(name, handler, set(types) if types else None, maxsize)
        with self._lock:
            self._subs.append(sub)
        if not self.synchronous:
            sub.thread = threading.Thread(target=self._consume, args=(sub,), daemon=True, name=f'consumer:{name}')
            sub.thread.start()
        return sub

    def _consume(self, sub: Subscription) -> None:
        while not self._closed:
            try:
                ev = sub.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._deliver(sub, ev)
            sub.queue.task_done()

    def _deliver(self, sub: Subscription, ev: dict) -> None:
        try:
            sub.handler(ev)
            sub.handled += 1
        except Exception as exc:  # one bad event must never stop a consumer
            sub.errors += 1
            if self.log_errors:
                print(f'  [stream] {sub.name} failed on {ev.get("type")}: {exc!r}')

    # ---- producers ----
    def publish(self, event_type: str, ts: float | None = None, **data) -> dict:
        ev = make_event(event_type, ts, **data)
        self.publish_event(ev)
        return ev

    def publish_event(self, ev: dict) -> None:
        self.published += 1
        with self._lock:
            subs = [s for s in self._subs if s.wants(ev['type'])]
        for sub in subs:
            if self.synchronous:
                self._deliver(sub, ev)
                continue
            try:
                sub.queue.put_nowait(ev)
            except queue.Full:
                # The consumer is far behind: drop its oldest event so the newest gets in.
                try:
                    sub.queue.get_nowait()
                    sub.queue.task_done()
                except queue.Empty:
                    pass
                sub.dropped += 1
                try:
                    sub.queue.put_nowait(ev)
                except queue.Full:
                    pass

    # ---- housekeeping ----
    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until every consumer has handled everything published so far (tests use this)."""
        end = time.time() + timeout
        while time.time() < end:
            if all(s.queue.unfinished_tasks == 0 for s in self._subs):
                return True
            time.sleep(0.01)
        return False

    def stats(self) -> list[dict]:
        return [{'name': s.name, 'handled': s.handled, 'waiting': s.queue.qsize(),
                 'dropped': s.dropped, 'errors': s.errors} for s in self._subs]

    def close(self) -> None:
        self._closed = True
