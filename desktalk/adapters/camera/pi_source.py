"""
Pictures from the Raspberry Pi: it POSTs each one to /api/pi/frame with what it knows (UploadInfo:
when it was taken, the light level, its own health, and what its detector found).

Besides the pictures, this keeps what the Pi said lately, for the app:
    pi_info()        what the Pi said about itself (/api/live → camera_info.pi)
    status()         that plus its detector and clock (/api/health)
    latest_upload()  what came with the newest picture, and when (Lens: a hot Pi, its detector)
    clock_off_sec()  how far the Pi's clock is off (Lens)

Each picture arrives a little after it was taken (it may wait for the Pi's detector, then the Wi-Fi), so the
Pi's clock is judged by the quickest of the last camera.pi_clock_pictures pictures, with the detection time
taken off: a slow upload doesn't make a correct clock look behind.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from typing import Any

from desktalk.adapters.camera.push_source import PushFrameSource
from desktalk.config import CameraSettings
from desktalk.domain import Frame, UploadInfo
from desktalk.ports import Clock


class PiFrameSource(PushFrameSource):
    kind = 'pi'

    def __init__(self, clock: Clock, camera: CameraSettings):
        super().__init__(clock, camera)
        self._last_frame_id = ''
        self._reported: dict[str, Any] = {}     # what the Pi said about itself; a value it stops sending is kept
        self._info: UploadInfo | None = None    # what came with the newest picture
        self._info_at = 0.0                     # when that picture arrived (this computer's clock)
        self._detect_ms: float | None = None    # the Pi's detector's last time
        self._model = ''
        self._clock_offs: deque[float] = deque(maxlen=camera.pi_clock_pictures)   # the Pi's clock minus ours

    def _took(self, frame: Frame) -> None:
        info = frame.pi
        if info is None:
            return
        self._info, self._info_at = info, frame.received_at
        self._last_frame_id = info.frame_id
        self._reported.update({k: v for k, v in asdict(info.health).items() if v not in (None, '')})
        if info.detect_ms is not None:
            self._detect_ms = info.detect_ms
        if info.model:
            self._model = info.model
        if info.taken_at is not None:
            waited = (info.detect_ms or 0.0) / 1000          # it waited this long for the Pi's detector
            self._clock_offs.append(info.taken_at + waited - frame.received_at)

    def pi_info(self) -> dict[str, Any]:
        """The newest picture's id and everything the Pi has said about itself (only what it sent)."""
        with self._cond:
            return {'last_frame_id': self._last_frame_id, **self._reported}

    def latest_upload(self) -> tuple[UploadInfo, float] | None:
        """What the Pi sent with its newest picture and when that picture arrived (this computer's clock).
        None before the first picture."""
        with self._cond:
            return (self._info, self._info_at) if self._info is not None else None

    def clock_off_sec(self) -> float | None:
        """The Pi's clock minus this computer's, in seconds (negative = behind), judged by the quickest of the
        recent pictures. None before a picture that said when it was taken."""
        with self._cond:
            return round(max(self._clock_offs), 1) if self._clock_offs else None

    def status(self) -> dict[str, Any]:
        """pi_info() plus the upload format, the Pi's detector (ok / busy / off and why, how long it took,
        which model) and how far the Pi's clock is off (seconds; negative = behind)."""
        with self._cond:
            info = self._info
            return {'last_frame_id': self._last_frame_id, **self._reported,
                    'upload_format': info.version if info else None,
                    'detector': info.detector.value if info else None,
                    'detector_reason': info.detector_reason if info else '',
                    'detect_ms': self._detect_ms, 'model': self._model,
                    'clock_off_sec': round(max(self._clock_offs), 1) if self._clock_offs else None}
