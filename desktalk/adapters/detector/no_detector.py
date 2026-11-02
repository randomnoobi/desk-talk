"""A Detector for an app that has none of its own (settings → detector.enabled: false), so it needs no PyTorch
or YOLO. On the Pi the detector container finds the objects; this one only says that nothing here can."""

from __future__ import annotations

from typing import Any

from desktalk.domain import Detection, DetectorUnavailable


class NoDetector:
    """A Detector (desktalk/ports.py) that never answers: detect() always raises DetectorUnavailable."""

    @property
    def label(self) -> str:
        return 'none (turned off in this app)'

    def detect(self, picture: Any) -> list[Detection]:
        raise DetectorUnavailable('This app has no detector of its own (settings → detector.enabled is off), '
                                  'and the camera sent no detections.')
