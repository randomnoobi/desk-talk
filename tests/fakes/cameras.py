"""Fakes for what the app asks the Pi and the phone (when they are the camera)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from desktalk.domain import Gravity, UploadInfo


class FakePi:
    """Implements PiStatus and PiUploads. A test hands it uploads with receive(info, arrived_at)."""

    def __init__(self):
        self._info: UploadInfo | None = None
        self._arrived_at = 0.0
        self._reported: dict[str, Any] = {}

    def receive(self, info: UploadInfo, arrived_at: float) -> None:
        self._info, self._arrived_at = info, arrived_at
        self._reported.update({k: v for k, v in asdict(info.health).items() if v not in (None, '')})

    def latest_upload(self) -> tuple[UploadInfo, float] | None:
        return (self._info, self._arrived_at) if self._info is not None else None

    def clock_off_sec(self) -> float | None:
        if self._info is None or self._info.taken_at is None:
            return None
        return round(self._info.taken_at - self._arrived_at, 1)

    def pi_info(self) -> dict[str, Any]:
        return {'last_frame_id': self._info.frame_id if self._info else '', **self._reported}

    def status(self) -> dict[str, Any]:
        info = self._info
        return {**self.pi_info(), 'upload_format': info.version if info else None,
                'detector': info.detector.value if info else None,
                'detector_reason': info.detector_reason if info else '',
                'detect_ms': info.detect_ms if info else None, 'model': info.model if info else '',
                'clock_off_sec': self.clock_off_sec()}


class FakePhone:
    """Implements PhoneCamera and PhoneControls. A tilt never gets too old here."""

    def __init__(self):
        self.gravity: Gravity | None = None
        self.rotation = 0
        self.stopped = False

    def set_motion(self, gravity: Gravity | None = None, rotation: int | None = None) -> None:
        if gravity is not None:
            self.gravity = gravity
        if rotation is not None:
            self.rotation = rotation

    def recent_gravity(self) -> Gravity | None:
        return self.gravity

    @property
    def stopped_by_user(self) -> bool:
        return self.stopped

    def mark_stopped(self) -> None:
        self.stopped = True

