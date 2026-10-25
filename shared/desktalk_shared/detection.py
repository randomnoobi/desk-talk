"""
One thing YOLO found in a picture: what it is, how sure it is, and where (a box in pixels).

The same types are used on the Pi (detector service, camera program) and on this computer,
so boxes and labels can't drift apart. As a dict (in uploads, the event log and the web API)
a detection looks like it always has:

    {"label": "cup", "conf": 0.83, "bbox": [x1, y1, x2, y2], "center": [cx, cy]}

Standard library only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BBox:
    """A box in pixels: (x1, y1) top left, (x2, y2) bottom right."""

    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self):
        for name in ('x1', 'y1', 'x2', 'y2'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'box {name} must be a number (got {value!r})')
        if self.x2 < self.x1:
            raise ValueError(f'box x2 ({self.x2}) is left of x1 ({self.x1})')
        if self.y2 < self.y1:
            raise ValueError(f'box y2 ({self.y2}) is above y1 ({self.y1})')

    @classmethod
    def from_list(cls, values: Sequence[Any]) -> BBox:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or len(values) != 4:
            raise ValueError(f'a box is 4 numbers [x1, y1, x2, y2] (got {values!r})')
        return cls(*values)

    def to_list(self) -> list[float]:
        return [round(float(v), 1) for v in (self.x1, self.y1, self.x2, self.y2)]

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    def clipped(self, width: float, height: float) -> BBox | None:
        """This box cut to a picture of the given size, or None if no part of it is inside."""
        x1, y1 = max(0.0, self.x1), max(0.0, self.y1)
        x2, y2 = min(float(width), self.x2), min(float(height), self.y2)
        if x2 <= x1 or y2 <= y1:
            return None
        return BBox(x1, y1, x2, y2)


@dataclass(frozen=True, slots=True)
class Detection:
    label: str            # what it is, in the model's words ("cup", "cell phone")
    confidence: float     # 0-1
    bbox: BBox

    def __post_init__(self):
        if not isinstance(self.label, str) or not self.label:
            raise ValueError(f'label must be a word (got {self.label!r})')
        c = self.confidence
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:
            raise ValueError(f'conf must be a number from 0 to 1 (got {c!r})')

    def to_dict(self) -> dict[str, Any]:
        cx, cy = self.bbox.center
        return {'label': self.label, 'conf': round(float(self.confidence), 3), 'bbox': self.bbox.to_list(),
                'center': [round(cx, 1), round(cy, 1)]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Detection:
        """Read a detection dict. Raises ValueError naming the bad field. 'center' is worked out, not read."""
        if not isinstance(data, Mapping):
            raise ValueError(f'a detection must be an object (got {data!r})')
        if 'bbox' not in data:
            raise ValueError('detection has no bbox')
        try:
            box = BBox.from_list(data['bbox'])
        except ValueError as exc:
            raise ValueError(f'bbox: {exc}') from None
        return cls(data.get('label'), data.get('conf'), box)


def keep_inside(detections: Iterable[Detection], width: float, height: float) -> list[Detection]:
    """Cut every box to the picture; drop detections with no part inside it."""
    out = []
    for d in detections:
        box = d.bbox.clipped(width, height)
        if box is not None:
            out.append(Detection(d.label, d.confidence, box))
    return out


class DetectorUnavailable(RuntimeError):
    """The detector can't answer right now (not started, model loading, crashed, too slow).
    The text says why in plain words."""
