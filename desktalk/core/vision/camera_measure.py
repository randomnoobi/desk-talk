"""
Lens's numbers: how bright, how much glare and how sharp a picture is, how much it changed since the last one
(all through the picture tools), which side a box is cut off at, how much of the picture a box covers, and how
far the phone tilted.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from desktalk.domain import Picture
from desktalk.ports import PictureTools


@dataclass(frozen=True)
class Reading:
    """What Lens measured in one picture."""

    brightness: float     # 0-255
    glare: float          # share (0-1) of pure-white pixels
    sharpness: float      # about 0 for a flat or very blurry picture


class CameraMeasure:
    """Measures a small gray copy of each picture (`width` pixels wide); `white_level` and brighter is glare."""

    def __init__(self, pictures: PictureTools, *, width: int, white_level: float):
        self._pictures = pictures
        self._width = width
        self._white_level = white_level

    def gray(self, picture: Picture) -> Picture:
        """The small gray copy everything is measured on."""
        return self._pictures.to_gray(self._pictures.shrink(picture, self._width))

    def reading(self, gray: Picture) -> Reading:
        tools = self._pictures
        return Reading(brightness=tools.brightness(gray), glare=tools.glare(gray, self._white_level),
                       sharpness=tools.sharpness(gray))

    def change(self, before: Picture, after: Picture) -> float:
        """The average change per pixel (0-255) between two gray copies. Copies of different sizes (the camera
        turned or was swapped) count as completely different (infinity), as in PictureTools.difference."""
        return self._pictures.difference(before, after)


def cut_off_side(box: Sequence[float], size: tuple[int, int], touch_px: float, fills: float) -> str | None:
    """Which side of the picture a box touches ('left', 'top' ...), or None. A box that fills most of the
    picture is never cut off: turning the camera wouldn't help."""
    x1, y1, x2, y2 = box
    fw, fh = size
    if (x2 - x1) > fills * fw or (y2 - y1) > fills * fh:
        return None
    gaps = {'left': x1, 'top': y1, 'right': fw - x2, 'bottom': fh - y2}
    side, gap = min(gaps.items(), key=lambda kv: kv[1])
    return side if gap <= touch_px else None


def box_share(box: Sequence[float], size: tuple[int, int]) -> float:
    """How much of the picture (0-1) a box covers. Only the part inside the picture counts."""
    x1, y1, x2, y2 = box
    fw, fh = size
    if fw <= 0 or fh <= 0:
        return 0.0
    w = max(0.0, min(x2, fw) - max(x1, 0))
    h = max(0.0, min(y2, fh) - max(y1, 0))
    return w * h / (fw * fh)


def tilt_degrees(g1: Sequence[float], g2: Sequence[float], *, trusted: float) -> float:
    """Degrees between two gravity readings (0 when either is weaker than `trusted`, in m/s²: settings →
    lens.trusted_gravity)."""
    a, b = math.hypot(*g1), math.hypot(*g2)
    if a < trusted or b < trusted:
        return 0.0
    cos = sum(x * y for x, y in zip(g1, g2, strict=True)) / (a * b)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))
