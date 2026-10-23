"""
Pictures from the phone page: it POSTs each one to /api/phone/frame (HTTPS). With each picture the
page also says how the phone is tilted (its motion sensor) and how far it turned the picture, so Lens
can tell when the phone got bumped.
"""

from __future__ import annotations

from desktalk.adapters.camera.push_source import PushFrameSource
from desktalk.config import CameraSettings
from desktalk.ports import Clock

Gravity = tuple[float, float, float]


class PhoneFrameSource(PushFrameSource):
    kind = 'phone'

    def __init__(self, clock: Clock, camera: CameraSettings):
        super().__init__(clock, camera)
        self._gravity: Gravity | None = None     # (x, y, z) from the phone's motion sensor, if it has one
        self._gravity_at = 0.0                   # clock.monotonic() when it came
        self._rotation = 0                       # how much the phone turned the picture (0 / 90 / 180 / 270)

    def set_motion(self, gravity: Gravity | None = None, rotation: int | None = None) -> None:
        """What the phone said with its newest picture. None = it didn't say (the old value stays)."""
        with self._cond:
            if gravity is not None:
                self._gravity = gravity
                self._gravity_at = self._clock.monotonic()
            if rotation is not None:
                self._rotation = rotation

    def recent_gravity(self) -> Gravity | None:
        """The phone's tilt, if it came recently enough (settings → camera.phone_tilt_max_age_sec)."""
        with self._cond:
            if self._gravity is None:
                return None
            fresh = self._clock.monotonic() - self._gravity_at < self._camera.phone_tilt_max_age_sec
            return self._gravity if fresh else None

    @property
    def rotation(self) -> int:
        with self._cond:
            return self._rotation
