"""A webcam on this computer (OpenCV), opened the first time a picture is asked for."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

import cv2

from desktalk.adapters.camera.speed import SpeedMeter
from desktalk.config import CameraSettings
from desktalk.domain import Frame
from desktalk.ports import Clock

log = logging.getLogger(__name__)


def open_webcam(index: int, platform: str = sys.platform) -> Any:
    """Open webcam `index` with OpenCV, or None if it won't open. On a Mac, AVFoundation is tried too
    when the default way fails."""
    backends = [None] + ([cv2.CAP_AVFOUNDATION] if platform == 'darwin' else [])
    for backend in backends:
        cap = cv2.VideoCapture(index) if backend is None else cv2.VideoCapture(index, backend)
        if cap.isOpened():
            return cap
        cap.release()
    return None


def _good(picture: Any) -> bool:
    return picture is not None and picture.size > 0


class WebcamSource:
    """A FrameSource (desktalk/ports.py). `open_capture(index)` gives an OpenCV-like capture or None.

    The chosen webcam is tried first, then the others (settings → camera.webcam_max_index): many USB
    webcams are number 1 on a Mac while 0 is the built-in one, and a webcam can open but give no pictures.
    If none works, it stops trying until the app restarts, as before."""

    kind = 'webcam'

    def __init__(self, clock: Clock, camera: CameraSettings, index: int,
                 open_capture: Callable[[int], Any] = open_webcam):
        self._clock = clock
        self._camera = camera
        self._open_capture = open_capture
        self.index = index                 # the webcam in use (another one is picked if this one fails)
        self._cap: Any = None
        self._failed = False
        self._waiting: Any = None          # the good picture the warm-up found, handed out first
        self._number = 0
        self._last_at: float | None = None
        self._speed = SpeedMeter(camera.fps_smoothing, camera.fps_gap_max_sec)

    def _try(self, index: int) -> bool:
        cap = self._open_capture(index)
        if cap is None:
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._camera.webcam_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._camera.webcam_height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)        # only the newest picture, not a queue of old ones
        for _ in range(self._camera.webcam_warmup_reads):     # USB webcams often need a few reads first
            ok, picture = cap.read()
            if ok and _good(picture):
                self._cap, self._waiting = cap, picture
                return True
        cap.release()
        return False

    def _open(self) -> None:
        order = [self.index] + [i for i in range(self._camera.webcam_max_index) if i != self.index]
        for index in order:
            if self._try(index):
                self.index = index
                log.info('Using webcam %d', index)
                return
        self._failed = True
        log.warning('No webcam gives pictures (tried %s). Check that one is plugged in, then restart Desk Talk',
                    ', '.join(str(i) for i in order))

    def read(self, timeout: float) -> Frame | None:
        """The next picture from the webcam; if there is none, waits `timeout` seconds and gives None."""
        if self._cap is None and not self._failed:
            self._open()
        picture = None
        if self._waiting is not None:
            picture, self._waiting = self._waiting, None
        elif self._cap is not None:
            ok, got = self._cap.read()
            if ok and _good(got):
                picture = got
        if picture is None:
            self._clock.sleep(timeout)
            return None
        self._number += 1
        self._last_at = self._clock.monotonic()
        self._speed.picture_at(self._last_at)
        return Frame(picture=picture, received_at=self._clock.now(), source=self.kind, number=self._number)

    @property
    def connected(self) -> bool:
        return (self._cap is not None and self._last_at is not None
                and self._clock.monotonic() - self._last_at < self._camera.disconnect_after_sec)

    @property
    def fps(self) -> float:
        """Pictures a second read lately (0 when not connected)."""
        return self._speed.fps if self.connected else 0.0

    def release(self) -> None:
        """Close the webcam (when Desk Talk stops)."""
        if self._cap is not None:
            self._cap.release()
