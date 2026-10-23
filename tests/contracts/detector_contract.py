"""What every Detector must do (desktalk.ports.Detector)."""

from __future__ import annotations

import numpy as np
import pytest

from desktalk.domain import Detection, DetectorUnavailable


class DetectorContract:
    """Subclass and give fixtures: `detector`, `broken_detector` (one that can't answer) and
    `picture` (a BGR numpy picture the detector finds at least one thing in)."""

    def test_label_is_plain_text(self, detector):
        assert isinstance(detector.label, str) and detector.label

    def test_finds_detections_inside_the_picture(self, detector, picture):
        found = detector.detect(picture)
        assert found and all(isinstance(d, Detection) for d in found)
        h, w = picture.shape[:2]
        for d in found:
            assert 0 <= d.bbox.x1 <= d.bbox.x2 <= w and 0 <= d.bbox.y1 <= d.bbox.y2 <= h

    def test_empty_picture_gives_a_list(self, detector):
        assert isinstance(detector.detect(np.zeros((64, 64, 3), np.uint8)), list)

    def test_unavailable_raises_detector_unavailable_with_a_reason(self, broken_detector, picture):
        with pytest.raises(DetectorUnavailable) as err:
            broken_detector.detect(picture)
        assert str(err.value)
