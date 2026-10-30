"""
TracePhotoTaker: keeps pictures of each object for its story (click something under "On the desk" on the app
page, or an object on the developer page's Live tab).

    when it appears              (an ObjectStateChanged 'appeared')         why='appeared'
    when it joins the chat       (an AgentJoined)                           why='joined'
    when it moves                (an ObjectStateChanged 'moved')            why='moved'     its box at the new spot
    when it goes away            (an ObjectStateChanged 'removed')          why='removed'   the newest picture, where
                                                                                            it's gone, with its last
                                                                                            box: the empty spot
    every object_story.photo_every_sec while it's there (PRESENT)           why='every'

An object the object states brought back as PRESENT at startup (restore() publishes no change) gets its 'every'
pictures too, counted from when the app started.

Each picture is the newest plain camera picture, with the object's box and confidence from the newest picture
that showed it (its most sure box there); a move or a going away uses the box the change itself names, when it
names one. Nothing is kept when there's no picture or no box yet. A failure is
logged and never stops the next picture.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Protocol

from desktalk.config import ObjectStorySettings
from desktalk.domain import TIMED_PHOTO, AgentJoined, Box, DetectionEvent, Event, ObjectStateChanged, Phase, TracePhoto
from desktalk.ports import Clock, EventBus, LivePictures, ObjectStatesView

log = logging.getLogger(__name__)

BOX_NUMBERS = 4                 # a box is left, top, right, bottom
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


def _box(numbers: tuple[float, ...] | None) -> Box | None:
    """A change's box; None when it names none."""
    if numbers is None or len(numbers) != BOX_NUMBERS:
        return None
    x1, y1, x2, y2 = (float(n) for n in numbers)
    return x1, y1, x2, y2


class TracePhotoTaker:
    def __init__(self, bus: EventBus, pictures: LivePictures, files: PhotoKeeper, settings: ObjectStorySettings,
                 clock: Clock, *, states: ObjectStatesView):
        self._pictures = pictures
        self._files = files
        self._every_sec = settings.photo_every_sec
        self._clock = clock
        self._seen: dict[str, _Seen] = {}           # label → its box in the newest picture that showed it
        self._last_photo: dict[str, float] = {}     # label → when its last picture was taken, while it's PRESENT
        started = clock.now()
        for label, seen in states.snapshot().objects.items():
            if seen.state == Phase.PRESENT:
                self._last_photo[label] = started       # brought back at startup: its 'every' pictures count from now
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
                self._take(label, TIMED_PHOTO)

    def _on_change(self, event: ObjectStateChanged) -> None:
        if event.change == 'appeared':
            self._last_photo[event.label] = self._clock.now()   # the 'every' pictures count from here
            self._take(event.label, 'appeared')
        elif event.change == 'moved':
            self._take(event.label, 'moved', _box(event.bbox))
        elif event.change == 'removed':
            self._take(event.label, 'removed', _box(event.bbox))     # the empty spot, with its last box
            self._last_photo.pop(event.label, None)
            self._seen.pop(event.label, None)                   # its old box must never land on a later picture

    def _take(self, label: str, why: str, box: Box | None = None) -> None:
        """`box`: where the change says it is (else its box in the newest picture that showed it)."""
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
                           why=why, confidence=seen.confidence, box=box or seen.box, size=seen.size)
        try:
            self._files.add(photo, jpeg)
        except Exception as exc:
            log.warning('Could not keep the %s trace picture of %s: %s', why, label, exc)
