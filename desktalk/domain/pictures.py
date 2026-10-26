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


@dataclass(frozen=True)
class TracePhoto:
    """A picture kept of one kind of object for the developer page's trace (the JPEG itself is kept apart)."""

    id: str
    label: str                                  # the detector's word, e.g. cup
    ts: float                                   # when it was taken
    why: str                                    # appeared | joined | every (one every dev.trace_photo_every_sec)
    confidence: float                           # how sure the detector was of the box
    box: Box                                    # the object's box in pixels
    size: tuple[int, int]                       # the picture's width and height
