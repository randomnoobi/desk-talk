"""
Cameras that send us pictures (the Pi, the phone). The web layer hands each upload to push();
the detection producer takes the newest one with read(). Only the newest picture is kept, so a
slow computer skips pictures instead of falling behind. A Pi picture marked busy (live view only)
waits in a place of its own, so it never pushes out a picture still waiting to be analysed (ok or
off); read() hands out the one to analyse first, then the busy one.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np

from desktalk.adapters.camera.speed import SpeedMeter
from desktalk.config import CameraSettings
from desktalk.domain import DetectorState, Frame, UploadInfo
from desktalk.ports import Clock


class PushFrameSource:
    """Holds the newest picture sent to us. A FrameSource and a PictureInbox (desktalk/ports.py)."""

    kind = ''

    def __init__(self, clock: Clock, camera: CameraSettings):
        self._clock = clock
        self._camera = camera
        self._cond = threading.Condition()
        self._to_analyse: Frame | None = None  # the newest picture to analyse, not handed out by read() yet
        self._live_only: Frame | None = None   # the newest busy Pi picture (live view only), not handed out yet
        self._number = 0                       # counts every picture that came in
        self._last_at: float | None = None     # clock.monotonic() of the newest picture
        self._speed = SpeedMeter(camera.fps_smoothing, camera.fps_gap_max_sec)
        self._stopped = False

    def push(self, jpeg: bytes, info: UploadInfo | None = None) -> bool:
        """A picture arrived. False if the bytes aren't a picture."""
        if not jpeg:
            return False                       # OpenCV crashes on an empty buffer
        picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if picture is None or picture.size == 0:
            return False
        with self._cond:
            self._number += 1
            self._last_at = self._clock.monotonic()
            self._speed.picture_at(self._last_at)
            self._stopped = False
            frame = Frame(picture=picture, received_at=self._clock.now(), source=self.kind, number=self._number,
                          jpeg=jpeg, pi=info)
            self._took(frame)
            if frame.detector_state is DetectorState.BUSY:
                self._live_only = frame
            else:
                self._to_analyse = frame
            self._cond.notify_all()
        return True

    def _took(self, frame: Frame) -> None:
        """For subclasses: a new picture was taken in (called with the lock held)."""

    def read(self, timeout: float) -> Frame | None:
        """The newest picture not handed out yet (one to analyse before a busy one); waits up to `timeout`
        seconds, then None."""
        with self._cond:
            if self._to_analyse is None and self._live_only is None:
                self._cond.wait(timeout)
            if self._to_analyse is not None:
                frame, self._to_analyse = self._to_analyse, None
            else:
                frame, self._live_only = self._live_only, None
            return frame

    def mark_stopped(self) -> None:
        """The user pressed Stop (as opposed to the pictures dropping out): not connected from now on."""
        with self._cond:
            self._stopped = True
            self._last_at = None

    @property
    def stopped_by_user(self) -> bool:
        with self._cond:
            return self._stopped

    @property
    def pictures_received(self) -> int:
        with self._cond:
            return self._number

    @property
    def connected(self) -> bool:
        with self._cond:
            last = self._last_at
        return last is not None and self._clock.monotonic() - last < self._camera.disconnect_after_sec

    @property
    def fps(self) -> float:
        """Pictures a second coming in lately (0 when not connected)."""
        if not self.connected:
            return 0.0
        with self._cond:
            return self._speed.fps
