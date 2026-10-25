"""LiveView: the newest pictures for the live view, per camera, and the light in the newest analysed one."""

from __future__ import annotations

import threading
from collections.abc import Mapping

from desktalk.domain import Light, Size

# What /api/live has always said about the picture size before the first picture came.
SIZE_BEFORE_THE_FIRST_PICTURE = Size(640, 480)


class LiveView:
    """Implements LivePictures."""

    def __init__(self, size: Size = SIZE_BEFORE_THE_FIRST_PICTURE):
        self._lock = threading.Lock()
        self._plain: dict[int, bytes] = {}
        self._boxed: dict[int, bytes | None] = {}
        self._size = size
        self._light: Light | None = None

    def put(self, cam: int, plain: bytes, boxed: bytes | None, size: Size) -> None:
        with self._lock:
            self._plain[cam] = plain
            self._boxed[cam] = boxed
            self._size = size

    def plain(self, cam: int | None = None) -> bytes | None:
        with self._lock:
            return _newest(self._plain, cam)

    def boxed(self, cam: int | None = None) -> bytes | None:
        with self._lock:
            return _newest(self._boxed, cam)

    def camera_numbers(self) -> list[int]:
        with self._lock:
            return sorted(self._plain)

    def size(self) -> Size:
        with self._lock:
            return self._size

    def set_light(self, brightness: float, lux: float | None) -> None:
        with self._lock:
            self._light = Light(brightness=brightness, lux=lux)

    def newest_light(self) -> Light | None:
        with self._lock:
            return self._light


def _newest(pictures: Mapping[int, bytes | None], cam: int | None) -> bytes | None:
    """`cam`'s picture, or the lowest-numbered camera's that has one. An empty picture counts as none."""
    if cam is not None:
        return pictures.get(cam) or None
    return next((pictures[c] for c in sorted(pictures) if pictures[c]), None)
