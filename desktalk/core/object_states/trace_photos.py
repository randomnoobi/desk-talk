"""
TracePhotoTaker: keeps pictures of each object for the developer page's trace (click an object on the Live tab).

    when it appears              (an ObjectStateChanged 'appeared')         why='appeared'
    when it joins the chat       (an AgentJoined)                           why='joined'
    every dev.trace_photo_every_sec while it's there (PRESENT)              why='every'

Each picture is the newest plain camera picture, with the object's box and confidence from the newest picture
that showed it (its most sure box there). Nothing is kept when there's no picture or no box yet. A failure is
logged and never stops the next picture.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Protocol

from desktalk.config import DevSettings
from desktalk.domain import AgentJoined, Box, DetectionEvent, Event, ObjectStateChanged, TracePhoto
from desktalk.ports import Clock, EventBus, LivePictures

log = logging.getLogger(__name__)

ID_LETTERS = 3                  # random bytes in a picture's id, so two taken in the same millisecond differ


class PhotoKeeper(Protocol):
    """Where the pictures go (TracePhotoFiles in the app)."""

    def add(self, photo: TracePhoto, jpeg: bytes) -> None:
        ...


@dataclass(frozen=True)
class _Seen:
    """An object's most sure box in the newest picture that showed it."""

    confidence: float
    box: Box
    size: tuple[int, int]


class TracePhotoTaker:
    def __init__(self, bus: EventBus, pictures: LivePictures, files: PhotoKeeper, settings: DevSettings,
                 clock: Clock):
        self._pictures = pictures
        self._files = files
        self._every_sec = settings.trace_photo_every_sec
        self._clock = clock
        self._seen: dict[str, _Seen] = {}           # label → its box in the newest picture that showed it
        self._last_photo: dict[str, float] = {}     # label → when its last picture was taken, while it's PRESENT
        bus.subscribe('trace-photos', self._on_event, types=[DetectionEvent, ObjectStateChanged, AgentJoined])

    def _on_event(self, event: Event) -> None:
        try:
            if isinstance(event, DetectionEvent):
                self._on_picture(event)
            elif isinstance(event, ObjectStateChanged):
                self._on_change(event)
            elif isinstance(event, AgentJoined):
                self._take(event.label, 'joined')
        except Exception:
            log.exception('Could not keep a trace picture for %s', type(event).__name__)

    def _on_picture(self, event: DetectionEvent) -> None:
        shown: set[str] = set()
        for found in sorted(event.detections, key=lambda d: d.confidence):     # the most sure box wins
            box = found.bbox
            self._seen[found.label] = _Seen(found.confidence, (box.x1, box.y1, box.x2, box.y2), event.size)
            shown.add(found.label)
        now = self._clock.now()
        for label, last in list(self._last_photo.items()):
            if label in shown and now - last >= self._every_sec:
                self._take(label, 'every')

    def _on_change(self, event: ObjectStateChanged) -> None:
        if event.change == 'appeared':
            self._last_photo[event.label] = self._clock.now()   # the 'every' pictures count from here
            self._take(event.label, 'appeared')
        elif event.change == 'removed':
            self._last_photo.pop(event.label, None)
            self._seen.pop(event.label, None)                   # its old box must never land on a later picture

    def _take(self, label: str, why: str) -> None:
        seen = self._seen.get(label)
        if seen is None:
            log.debug('No box of %s seen yet, so no %s trace picture', label, why)
            return
        jpeg = self._pictures.plain()
        if jpeg is None:
            log.debug('No camera picture yet, so no %s trace picture of %s', why, label)
            return
        now = self._clock.now()
        if label in self._last_photo:
            self._last_photo[label] = now
        photo = TracePhoto(id=f'{round(now * 1000)}-{why}-{secrets.token_hex(ID_LETTERS)}', label=label, ts=now,
                           why=why, confidence=seen.confidence, box=seen.box, size=seen.size)
        try:
            self._files.add(photo, jpeg)
        except Exception as exc:
            log.warning('Could not keep the %s trace picture of %s: %s', why, label, exc)
