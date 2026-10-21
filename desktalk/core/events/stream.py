"""
The event stream: producer → stream → consumers.

Producers publish typed events (a picture was analysed, an agent joined, a sip was logged ...).
Each consumer subscribes with its own queue and thread, so a slow consumer (say, the AI) never
holds up the camera or the other consumers.

    stream = EventStream(config.event_stream)
    stream.subscribe('event-log', event_log.write)                         # every event
    stream.subscribe('roster', roster.on_detection, types={DetectionEvent})
    stream.publish(AgentJoined(ts=clock.now(), agent='cup', name='Cup', label='cup', conf=0.9))
    ...
    stream.wait_idle(timeout=5)      # at shutdown: let the consumers finish what's waiting
    stream.close(timeout=5)          # then stop their threads

When a consumer falls far behind (settings → event_stream.queue_size events waiting), its oldest
picture event is dropped to make room and counted in stats(). Joins, leaves, alerts, habits and
nudges are never dropped. A handler that raises is logged and counted; its consumer keeps going.

synchronous=True runs the handlers right inside publish(), in the order they subscribed, with no
threads. Tests use it so results don't depend on thread timing.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from desktalk.config import EventStreamSettings
from desktalk.domain import DetectionEvent, Event
from desktalk.runtime.errors import plain_error

log = logging.getLogger(__name__)


class _Consumer:
    """One subscriber: its handler, its queue, its thread and its counts. Guarded by the stream's lock."""

    def __init__(self, name: str, handler: Callable[[Event], None], types: tuple[type[Event], ...] | None,
                 lock: threading.Lock):
        self.name = name
        self.handler = handler
        self.types = types
        self.waiting: deque[Event] = deque()
        self.has_work = threading.Condition(lock)
        self.busy = False                          # handling an event right now
        self.thread: threading.Thread | None = None
        self.handled = 0
        self.dropped = 0
        self.errors = 0
        self.errors_in_row = 0

    def wants(self, event: Event) -> bool:
        return self.types is None or isinstance(event, self.types)


class EventStream:
    """The EventBus (desktalk/ports.py): in-process publish / subscribe with typed events."""

    def __init__(self, settings: EventStreamSettings, *, synchronous: bool = False):
        self._queue_size = settings.queue_size
        self._synchronous = synchronous
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._consumers: list[_Consumer] = []
        self._closed = False

    def subscribe(self, name: str, handler: Callable[[Event], None],
                  types: Iterable[type[Event]] | None = None) -> None:
        """Call `handler` with every event of the given classes (None = every event), in the order they
        were published. `name` is how stats() and the log refer to this consumer."""
        consumer = _Consumer(name, handler, tuple(types) if types is not None else None, self._lock)
        with self._lock:
            self._consumers.append(consumer)
        if not self._synchronous:
            consumer.thread = threading.Thread(target=self._run, args=(consumer,), name=f'events:{name}',
                                               daemon=True)
            consumer.thread.start()

    def publish(self, event: Event) -> None:
        """Hand the event to every consumer that wants it. Never waits for a consumer (unless synchronous).
        After close() it does nothing."""
        with self._lock:
            if self._closed:
                return
            consumers = [c for c in self._consumers if c.wants(event)]
            if not self._synchronous:
                for consumer in consumers:
                    self._add(consumer, event)
                return
        for consumer in consumers:
            self._deliver(consumer, event)

    def _add(self, consumer: _Consumer, event: Event) -> None:
        """Put an event on a consumer's queue (the lock is held). A full queue makes room by dropping
        its oldest picture; with no picture waiting, a new picture is dropped and anything else
        goes in anyway, so joins, alerts, habits and nudges are never lost."""
        if len(consumer.waiting) >= self._queue_size:
            oldest_picture = next((i for i, e in enumerate(consumer.waiting) if isinstance(e, DetectionEvent)), None)
            if oldest_picture is not None:
                del consumer.waiting[oldest_picture]
                consumer.dropped += 1
            elif isinstance(event, DetectionEvent):
                consumer.dropped += 1
                return
        consumer.waiting.append(event)
        consumer.has_work.notify()

    def _run(self, consumer: _Consumer) -> None:
        while True:
            with self._lock:
                while not consumer.waiting and not self._closed:
                    consumer.has_work.wait()
                if self._closed:
                    return
                event = consumer.waiting.popleft()
                consumer.busy = True
            self._deliver(consumer, event)
            with self._lock:
                consumer.busy = False
                self._idle.notify_all()

    def _deliver(self, consumer: _Consumer, event: Event) -> None:
        try:
            consumer.handler(event)
        except Exception as exc:  # one bad event must never stop a consumer
            with self._lock:
                consumer.errors += 1
                consumer.errors_in_row += 1
                first = consumer.errors_in_row == 1
            # The full traceback only when it starts failing, so a broken handler doesn't flood the log.
            log.error('%s could not handle a %s event: %s', consumer.name, event.TYPE, plain_error(exc),
                      exc_info=first)
            return
        with self._lock:
            consumer.handled += 1
            consumer.errors_in_row = 0

    def wait_idle(self, timeout: float) -> bool:
        """Wait until every consumer has handled everything published so far, including events their
        handlers publish meanwhile. False if that took longer than `timeout` seconds."""
        with self._lock:
            return self._idle.wait_for(lambda: not any(c.waiting or c.busy for c in self._consumers), timeout)

    def stats(self) -> list[dict[str, Any]]:
        """Per consumer, in the order they subscribed: name, handled, waiting, dropped, errors."""
        with self._lock:
            return [{'name': c.name, 'handled': c.handled, 'waiting': len(c.waiting), 'dropped': c.dropped,
                     'errors': c.errors} for c in self._consumers]

    def close(self, timeout: float) -> list[str]:
        """Stop the consumer threads, waiting up to `timeout` seconds for each. A consumer in the middle of
        an event finishes it first; events still waiting are not handled (call wait_idle() before to
        finish them). Returns the names of the consumers still busy after their wait (their threads
        are daemons, so they don't keep the app from closing)."""
        with self._lock:
            self._closed = True
            consumers = list(self._consumers)
            for consumer in consumers:
                consumer.has_work.notify()
        still_running = []
        for consumer in consumers:
            if consumer.thread:
                consumer.thread.join(timeout)
                if consumer.thread.is_alive():
                    still_running.append(consumer.name)
        return still_running
