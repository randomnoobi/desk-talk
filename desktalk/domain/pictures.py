"""Plain names for pictures and the shapes drawn on them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Picture = Any                               # a numpy array: BGR colour (OpenCV's layout) or one gray channel
Box = tuple[float, float, float, float]     # x1, y1, x2, y2 in pixels: top left, then bottom right
Point = tuple[float, float]                 # x, y
Gravity = tuple[float, float, float]        # which way is down for the phone, from its motion sensor


@dataclass(frozen=True)
class Size:
    """How big a picture is, in pixels."""

    width: int
    height: int


TIMED_PHOTO = 'every'           # a TracePhoto's `why` when it was taken because time passed, not because of a step


@dataclass(frozen=True)
class TracePhoto:
    """A picture kept of one kind of object for its story (the JPEG itself is kept apart)."""

    id: str
    label: str                                  # the detector's word, e.g. cup
    ts: float                                   # when it was taken
    why: str                                    # appeared | joined | moved | removed (it went away: the empty spot,
                                                # with its last box) | every (one every object_story.photo_every_sec)
    confidence: float                           # how sure the detector was of the box
    box: Box                                    # the object's box in pixels
    size: tuple[int, int]                       # the picture's width and height


def newest_trace_photos(photos: list[TracePhoto], timed_kept: int, steps_kept: int) -> list[TracePhoto]:
    """The ones to keep, oldest first: the newest `timed_kept` timed pictures and, apart from them, the newest
    `steps_kept` step pictures, so a long stay's timed pictures never push out how the object got there."""
    ordered = sorted(photos, key=lambda p: p.ts)
    timed = [p for p in ordered if p.why == TIMED_PHOTO][-timed_kept:]
    steps = [p for p in ordered if p.why != TIMED_PHOTO][-steps_kept:]
    return sorted(timed + steps, key=lambda p: p.ts)
