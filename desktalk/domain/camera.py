"""A picture from a camera, with what came with it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from desktalk_shared.detection import Detection
from desktalk_shared.wire import DetectorState, UploadInfo


@dataclass(frozen=True, kw_only=True)
class Frame:
    picture: Any                    # numpy array, BGR (OpenCV's layout)
    received_at: float              # this computer's clock
    source: str                     # pi, phone or webcam
    number: int                     # counts up from 1 for each new picture from this source
    jpeg: bytes | None = None       # the original JPEG, when the camera sent one
    pi: UploadInfo | None = None    # what the Pi sent with it (Pi pictures only)

    @property
    def detector_state(self) -> DetectorState | None:
        """ok / busy / off from the Pi's detector; None = this camera has no detector, detect here."""
        return self.pi.detector if self.pi else None

    @property
    def pi_detections(self) -> tuple[Detection, ...]:
        return self.pi.detections if self.pi else ()

    @property
    def frame_id(self) -> str:
        if self.pi and self.pi.frame_id:
            return self.pi.frame_id
        return f'{self.source}-{self.number:06d}'
