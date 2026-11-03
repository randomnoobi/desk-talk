"""A Detector for an app that has none of its own (settings → detector.enabled: false), so it needs no PyTorch
or YOLO. On the Pi the detector container finds the objects; this one only says that nothing here can."""

from __future__ import annotations

from typing import Any

from desktalk.config.prompts import DetectionWords
from desktalk.domain import Detection, DetectorUnavailable


class NoDetector:
    """A Detector (desktalk/ports.py) that never answers: detect() always raises DetectorUnavailable.
    `words`: config/prompts.yaml → detection."""

    def __init__(self, words: DetectionWords):
        self._words = words

    @property
    def label(self) -> str:
        return self._words.none_label

    def detect(self, picture: Any) -> list[Detection]:
        raise DetectorUnavailable(self._words.none_reason)
