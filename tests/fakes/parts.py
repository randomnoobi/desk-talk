"""Fakes for the interfaces in desktalk/ports.py. Each passes the same contract tests as the real part."""

from __future__ import annotations

import copy
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

import cv2
import numpy as np

from desktalk.domain import (
    CHANGE_TYPES,
    AIUnavailable,
    Detection,
    DetectorUnavailable,
    Event,
    Frame,
    StateRecord,
    UploadInfo,
)

E = TypeVar('E', bound=Event)


class FakeDetector:
    """Returns what the test scripted: a list of Detections, or a function picture → list.
    `fail` makes every call raise DetectorUnavailable(fail). With a FakeClock and `delay`, each call
    moves the clock forward by `delay` seconds (a slow detector, without waiting)."""

    label = 'fake detector'

    def __init__(self, detections: Sequence[Detection] | Callable[[Any], list[Detection]] = (),
                 fail: str = '', clock=None, delay: float = 0.0):
        self.detections = detections
        self.fail = fail
        self.clock = clock
        self.delay = delay
        self.calls = 0

    def detect(self, picture) -> list[Detection]:
        self.calls += 1
        if self.clock is not None and self.delay:
            self.clock.advance(self.delay)
        if self.fail:
            raise DetectorUnavailable(self.fail)
        found = self.detections(picture) if callable(self.detections) else self.detections
        return list(found)


class FakeFrameSource:
    """A camera the test controls. add(picture) is a new picture from the camera; push(jpeg) is an
    upload (like the Pi or phone). Only the newest picture is kept, like the real ones."""

    def __init__(self, kind: str = 'fake', clock=None):
        self.kind = kind
        self.clock = clock
        self._next: Frame | None = None
        self._number = 0
        self.connected = False
        self.fps = 0.0

    def _now(self) -> float:
        return self.clock.now() if self.clock is not None else time.time()

    def add(self, picture, jpeg: bytes | None = None, info: UploadInfo | None = None) -> Frame:
        self._number += 1
        self._next = Frame(picture=picture, received_at=self._now(), source=self.kind, number=self._number,
                           jpeg=jpeg, pi=info)
        self.connected = True
        return self._next

    def push(self, jpeg: bytes, info: UploadInfo | None = None) -> bool:
        picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if picture is None or picture.size == 0:
            return False
        self.add(picture, jpeg=jpeg, info=info)
        return True

    def read(self, timeout: float) -> Frame | None:
        frame, self._next = self._next, None
        return frame


class SyncEventBus:
    """Delivers each event right inside publish(), in subscribe order. No threads, so tests are exact."""

    def __init__(self):
        self._subs: list[dict[str, Any]] = []
        self.published: list[Event] = []

    def subscribe(self, name: str, handler: Callable[[Event], None],
                  types: Iterable[type[Event]] | None = None) -> None:
        self._subs.append({'name': name, 'handler': handler, 'types': tuple(types) if types else None,
                           'handled': 0, 'errors': 0})

    def publish(self, event: Event) -> None:
        self.published.append(event)
        for sub in self._subs:
            if sub['types'] is not None and not isinstance(event, sub['types']):
                continue
            try:
                sub['handler'](event)
                sub['handled'] += 1
            except Exception:
                sub['errors'] += 1

    def wait_idle(self, timeout: float) -> bool:
        return True

    def stats(self) -> list[dict[str, Any]]:
        return [{'name': s['name'], 'handled': s['handled'], 'waiting': 0, 'dropped': 0, 'errors': s['errors']}
                for s in self._subs]

    def close(self, timeout: float) -> list[str]:
        return []         # no threads: nothing is ever still busy

    def of_type(self, cls: type[E]) -> list[E]:
        """Everything published of one type (handy in asserts)."""
        return [e for e in self.published if isinstance(e, cls)]


def _day(ts: float) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(ts))


class InMemoryEventStore:
    """The event log, kept in memory. Picture events go to `detections`, the rest to `events`."""

    def __init__(self):
        self._events: dict[str, list[dict]] = {}
        self._detections: dict[str, list[dict]] = {}
        self.compactions = 0
        self.folder = Path('in memory')
        self.last_compaction: dict[str, Any] | None = None

    def write(self, event: Event) -> None:
        d = event.to_dict()
        target = self._detections if d['type'] == 'detection' else self._events
        target.setdefault(_day(event.ts), []).append(d)

    def days(self) -> list[str]:
        return sorted(set(self._events) | set(self._detections), reverse=True)

    def events(self, day: str, types: set[str] | None, limit: int) -> list[dict]:
        evs = [e for e in self._events.get(day, []) if not types or e['type'] in types]
        return list(reversed(evs))[:limit]

    def presence(self, day: str, include_live: bool = True) -> list[dict]:
        return []

    def recent_detections(self, day: str, limit: int) -> list[dict]:
        return list(reversed(self._detections.get(day, [])))[:limit]

    def counts(self, day: str) -> dict:
        by_type: dict[str, int] = {}
        for e in self._events.get(day, []):
            by_type[e['type']] = by_type.get(e['type'], 0) + 1
        return {'pictures_waiting': len(self._detections.get(day, [])), 'events': by_type}

    def compact(self, now: float) -> dict:
        self.compactions += 1
        frames = sum(len(v) for v in self._detections.values())
        self._detections.clear()
        summary = {'frames': frames, 'intervals': 0, 'from': None, 'to': None}
        self.last_compaction = {'ts': now, **summary}
        return summary

    def prune(self, now: float) -> list[str]:
        return []


class InMemoryKeyValueStore:
    def __init__(self, data: Mapping[str, Any] | None = None):
        self.data: dict[str, Any] = copy.deepcopy(dict(data or {}))
        self.saves = 0

    def load(self, key: str, default: Any = None) -> Any:
        return copy.deepcopy(self.data.get(key, default))

    def save(self, key: str, value: Any) -> None:
        self.saves += 1
        self.data[key] = copy.deepcopy(value)

    def keys(self, prefix: str = '') -> list[str]:
        return sorted(k for k in self.data if k.startswith(prefix))


class InMemoryStateStore:
    """The object state history in a list: the same answers as the SQLite store, nothing written to disk."""

    def __init__(self):
        self.records: list[StateRecord] = []
        self.closed = False

    def append(self, records: Sequence[StateRecord]) -> None:
        self.records.extend(records)

    def latest_states(self, since: float) -> dict[str, StateRecord]:
        latest: dict[str, StateRecord] = {}
        for record in self.records:
            if record.type == 'moved' or record.ts < since:
                continue
            if record.label not in latest or record.ts >= latest[record.label].ts:
                latest[record.label] = record
        return latest

    def changes(self, since: float) -> list[StateRecord]:
        return sorted((r for r in self.records if r.type in CHANGE_TYPES and r.ts > since), key=lambda r: r.ts)

    def close(self) -> None:
        self.closed = True


class FakeChatModel:
    """The AI, scripted. `reply` is a string, a list of strings (one per call, last one repeats) or a
    function of the messages. `fail` makes every call raise AIUnavailable(fail). Records every call."""

    label = 'Fake AI'

    def __init__(self, reply: str | list[str] | Callable[[Sequence[Mapping[str, Any]]], str] = 'Hi there!',
                 fail: str = '', rate_limited: bool = False, ready: bool = True, is_local: bool = False,
                 local_vision: bool = False):
        self.is_local = is_local
        self.local_vision = local_vision
        self.reply = reply
        self.fail = fail
        self.rate_limited = rate_limited
        self.ready = ready
        self.calls: list[dict[str, Any]] = []
        self._problem: str | None = None

    def _answer(self, call: dict[str, Any]) -> str:
        self.calls.append(call)
        if self.fail:
            self._problem = self.fail
            raise AIUnavailable(self.fail, rate_limited=self.rate_limited)
        self._problem = None
        if callable(self.reply):
            return self.reply(call.get('messages', []))
        if isinstance(self.reply, list):
            return self.reply[min(len(self.calls), len(self.reply)) - 1]
        return self.reply

    def chat(self, messages, *, max_tokens: int, temperature: float, background: bool = False) -> str:
        return self._answer({'kind': 'chat', 'messages': list(messages), 'max_tokens': max_tokens,
                             'temperature': temperature, 'background': background})

    def vision(self, jpeg: bytes, prompt: str, *, background: bool = True) -> str:
        return self._answer({'kind': 'vision', 'jpeg': jpeg, 'prompt': prompt, 'background': background})

    def problem(self) -> str | None:
        return self._problem


class RecordingNotifier:
    """Keeps every message sent to the app pages."""

    def __init__(self, clock=None):
        self.clock = clock
        self.sent: list[dict[str, Any]] = []

    def send(self, message: Mapping[str, Any]) -> dict[str, Any]:
        msg = dict(message)
        msg.setdefault('id', uuid.uuid4().hex[:12])
        msg.setdefault('ts', self.clock.now() if self.clock is not None else time.time())
        self.sent.append(msg)
        return msg

    def texts(self) -> list[str]:
        return [m.get('text', '') for m in self.sent]


class FakeDesktopNotifications:
    """Shows nothing: `shown` records each (title, text) it was asked to show, in order."""

    def __init__(self):
        self.shown: list[tuple[str, str]] = []

    def show(self, title: str, text: str) -> None:
        self.shown.append((title, text))
