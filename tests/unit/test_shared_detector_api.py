"""The detector service's HTTP API shapes (desktalk_shared.detector_api)."""

from __future__ import annotations

import pytest

from desktalk_shared.detection import BBox, Detection
from desktalk_shared.detector_api import DetectorHealth, DetectReply, ModelSettings, ServiceState
from desktalk_shared.wire import WireError

MODEL = ModelSettings(model='yolo11s.pt', format='ncnn', image_size=480, confidence=0.35, half=True)


def test_model_settings_round_trip():
    assert ModelSettings.from_dict(MODEL.to_dict()) == MODEL


@pytest.mark.parametrize('change, word', [
    ({'format': 'tflite'}, 'format'),
    ({'image_size': 500}, 'image_size'),
    ({'image_size': 0}, 'image_size'),
    ({'confidence': 1.2}, 'confidence'),
    ({'format': 'pt', 'half': True}, 'half'),
    ({'model': ''}, 'model'),
])
def test_model_settings_reject_bad_values(change, word):
    data = {**MODEL.to_dict(), **change}
    with pytest.raises(WireError, match=word):
        ModelSettings.from_dict(data)


def test_health_round_trip_with_and_without_model():
    ready = DetectorHealth(state=ServiceState.READY, model=MODEL, message='', detections_done=10, last_detect_ms=388.0)
    starting = DetectorHealth(state=ServiceState.STARTING, model=None, message='converting yolo11s.pt to ncnn')
    for h in (ready, starting):
        assert DetectorHealth.from_dict(h.to_dict()) == h
    assert ready.to_dict()['state'] == 'ready'


def test_detect_reply_round_trip():
    reply = DetectReply(detections=(Detection('cup', 0.8, BBox(1, 2, 3, 4)),), detect_ms=401.2,
                        width=1280, height=720, model='yolo11s.pt (ncnn, 480)')
    assert DetectReply.from_dict(reply.to_dict()) == reply


def test_detect_reply_with_garbage_raises_wire_error():
    with pytest.raises(WireError):
        DetectReply.from_dict({'detections': 'lots'})
    with pytest.raises(WireError):
        DetectorHealth.from_dict({'state': 'dancing'})
