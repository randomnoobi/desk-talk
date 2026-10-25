"""Fakes for the picture tools, the photo taker and the desk rules. No OpenCV: pictures are plain numpy arrays."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from desktalk.domain import Box, DeskRules, Picture, Size, Zone

FAKE_JPEG = b'FAKEJPEG'                  # what starts every "JPEG" FakePictureTools makes
HEADER = struct.Struct('>III')            # height, width, colour channels (0 = gray)
GRAY_WEIGHTS = (0.114, 0.587, 0.299)      # blue, green, red: the same mix OpenCV uses


class FakePictureTools:
    """Implements PictureTools with numpy alone. Its "JPEGs" are its own made-up format (FAKE_JPEG, the size,
    then every pixel), so they come back exactly; real JPEG bytes don't read (decode_jpeg gives None)."""

    def decode_jpeg(self, jpeg: bytes, *, gray: bool = False) -> Picture | None:
        start = len(FAKE_JPEG)
        if not jpeg.startswith(FAKE_JPEG) or len(jpeg) < start + HEADER.size:
            return None
        height, width, channels = HEADER.unpack_from(jpeg, start)
        pixels = np.frombuffer(jpeg, np.uint8, offset=start + HEADER.size)
        shape = (height, width, channels) if channels else (height, width)
        if pixels.size != math.prod(shape):
            return None
        picture = pixels.reshape(shape).copy()
        return self.to_gray(picture) if gray else picture

    def encode_jpeg(self, picture: Picture, quality: int) -> bytes | None:
        picture = np.asarray(picture, np.uint8)
        if picture.size == 0:
            return None
        height, width = picture.shape[:2]
        channels = picture.shape[2] if picture.ndim == 3 else 0
        return FAKE_JPEG + HEADER.pack(height, width, channels) + picture.tobytes()

    def size(self, picture: Picture) -> Size:
        height, width = np.asarray(picture).shape[:2]
        return Size(int(width), int(height))

    def shrink(self, picture: Picture, width: int, height: int | None = None) -> Picture:
        picture = np.asarray(picture)
        h, w = picture.shape[:2]
        height = height if height is not None else max(1, round(h * width / max(1, w)))
        rows = np.arange(height) * h // height
        cols = np.arange(width) * w // width
        return picture[rows][:, cols]

    def to_gray(self, picture: Picture) -> Picture:
        picture = np.asarray(picture)
        if picture.ndim == 2:
            return picture
        mixed = picture[..., :3].astype(np.float64) @ np.array(GRAY_WEIGHTS)
        return np.clip(np.rint(mixed), 0, 255).astype(np.uint8)

    def crop(self, picture: Picture, box: Box) -> Picture:
        picture = np.asarray(picture)
        h, w = picture.shape[:2]
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = min(w, int(box[2])), min(h, int(box[3]))
        return picture[y1:y2, x1:x2]

    def brightness(self, gray: Picture) -> float:
        return float(np.asarray(gray).mean())

    def glare(self, gray: Picture, white_level: float) -> float:
        return float((np.asarray(gray) >= white_level).mean())

    def difference(self, a: Picture, b: Picture) -> float:
        a, b = np.asarray(a), np.asarray(b)
        if a.shape != b.shape:
            return math.inf
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())

    def sharpness(self, gray: Picture) -> float:
        """How much each pixel differs from its four neighbours (like OpenCV's Laplacian), spread as a variance."""
        g = np.asarray(gray, np.float64)
        if g.shape[0] < 3 or g.shape[1] < 3:
            return 0.0
        edges = g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1]
        return float(edges.var())


class FakePhotoTaker:
    """Implements PhotoTaker: remembers each save in `saved` and hands out photo-1, photo-2 ..."""

    def __init__(self, has_picture: bool = True):
        self.has_picture = has_picture
        self.saved: list[tuple[str, tuple[Box, ...]]] = []

    def save(self, label: str, crop_boxes: Sequence[Box] = ()) -> str | None:
        if not self.has_picture:
            return None
        self.saved.append((label, tuple(crop_boxes)))
        return f'photo-{len(self.saved)}'


def plain_desk_rules(**changes: Any) -> DeskRules:
    """A small set of danger rules for tests (not the ones in config/settings.yaml), with `changes` on top."""
    rules = DeskRules(
        outline=(), reference_gravity=None, edge_danger_px=35, edge_warn_px=75,
        edge_objects=frozenset({'cup', 'bottle'}), zones={'laptop': Zone(80, 'Spill / pressure zone')},
        zone_intruders={'laptop': frozenset({'cup', 'bottle'})}, harmless_pairs=frozenset(),
        path_ratio=0.3, crowd_radius_px=140, crowd_min_objects=3, harmless_crowd=frozenset(),
        history_length=30, approach_min_frames=8, approach_rate=0.12, repeated_times=3, repeated_window_sec=60,
        risk_weights={'zone_intrusion': 2, 'edge_danger': 2}, risk_level_steps=(1, 3, 5, 7),
        desk_border_share=0.03, footprint_share=0.25, footprint_min_px=12,
    )
    return replace(rules, **changes)


class FakeDeskRules:
    """Implements DeskRulesView (and DeskLayout) over plain_desk_rules(), keeping saves in `saved`."""

    def __init__(self, rules: DeskRules | None = None, *, max_corners: int = 12):
        self._built_in = rules or plain_desk_rules()
        self._max_corners = max_corners
        self.saved: dict[str, Any] | None = None
        self._current = self._built_in

    @property
    def current(self) -> DeskRules:
        return self._current

    @property
    def loaded(self) -> bool:
        return self.saved is not None

    def has_outline(self) -> bool:
        return bool(self._current.outline)

    def reference_gravity(self) -> tuple[float, ...] | None:
        return self._current.reference_gravity

    def save(self, changes: Mapping[str, Any]) -> None:
        self.saved = {**(self.saved or {}), **changes}
        corners = tuple((float(x), float(y)) for x, y in self.saved.get('desk_outline') or [])
        outline = corners if len(corners) >= 3 else ()
        gravity = (self.saved.get('camera_reference') or {}).get('gravity')
        self._current = replace(self._built_in, outline=outline,
                                reference_gravity=tuple(gravity) if gravity and outline else None)

    def clean_outline(self, points: Any) -> list[list[float]]:
        try:
            corners = [[min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))] for x, y in (points or [])]
        except (TypeError, ValueError):
            raise ValueError('desk_outline must be a list of [x, y] points') from None
        if corners and len(corners) < 3:
            raise ValueError('Mark at least 3 corners of the desk')
        return corners[:self._max_corners]

    def for_settings_page(self) -> dict[str, Any]:
        saved = self.saved or {}
        return {'loaded': self.loaded, 'desk_outline': saved.get('desk_outline', []),
                'danger_zones': saved.get('danger_zones', {}), 'edge_proximity': saved.get('edge_proximity', {}),
                'chain_reaction': saved.get('chain_reaction', {}), 'temporal': saved.get('temporal', {})}
