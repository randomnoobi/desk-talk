"""The detector service's web API (FastAPI's TestClient, with a fake YOLO): /detect, /health, /model."""

from __future__ import annotations

import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from desktalk_detector.app import create_app
from desktalk_detector.manager import ModelManager
from desktalk_detector.model_folder import ModelFolder
from desktalk_shared.detector_api import DetectorHealth, DetectReply, ModelSettings, ServiceState
from tests.unit.detector_service_fakes import FakeYolo

MODEL = ModelSettings(model='yolo11n.pt', format='ncnn', image_size=480, confidence=0.5, half=False)
LIMIT = 50_000      # bytes


class StepClock:
    def __init__(self, step: float = 0.025):
        self.now, self.step = 100.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.fixture
def yolo():
    return FakeYolo([('cup', 0.9, (10, 20, 50, 60))])


@pytest.fixture
def folder(tmp_path, yolo):
    (tmp_path / 'yolo11n.pt').write_bytes(b'weights')
    return ModelFolder(tmp_path, yolo)


@pytest.fixture
def manager(folder):
    return ModelManager(folder, retry_first_sec=5, retry_max_sec=40, clock=StepClock())


@pytest.fixture
def client(manager):
    """No `with`: the background loader doesn't start, so tests load models with manager.load_step()."""
    return TestClient(create_app(manager, max_picture_bytes=LIMIT))


@pytest.fixture
def ready(manager):
    manager.want(MODEL)
    manager.load_step()
    return manager


def jpeg(height=480, width=640) -> bytes:
    picture = np.full((height, width, 3), 128, np.uint8)
    return cv2.imencode('.jpg', picture)[1].tobytes()


def post_picture(client, body: bytes):
    return client.post('/detect', content=body, headers={'Content-Type': 'image/jpeg'})


def test_health_says_what_the_manager_says(client, manager):
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json() == manager.health().to_dict()
    assert DetectorHealth.from_dict(r.json()).state is ServiceState.STARTING


def test_detect_replies_with_what_the_model_found_in_the_shared_shape(client, ready):
    r = post_picture(client, jpeg(height=480, width=640))
    assert r.status_code == 200
    assert r.json() == {'detections': [{'label': 'cup', 'conf': 0.9, 'bbox': [10.0, 20.0, 50.0, 60.0],
                                        'center': [30.0, 40.0]}],
                        'detect_ms': 25.0, 'width': 640, 'height': 480, 'model': 'yolo11n.pt (ncnn, 480)'}
    assert DetectReply.from_dict(r.json()).detections[0].label == 'cup'


def test_the_model_gets_the_decoded_picture(client, ready, yolo):
    post_picture(client, jpeg(height=72, width=128))
    assert yolo.predictions[-1]['shape'] == (72, 128, 3)


def test_detect_without_a_model_is_503_with_the_reason(client):
    r = post_picture(client, jpeg())
    assert r.status_code == 503
    assert 'no model' in r.json()['detail']


@pytest.mark.parametrize('body', [b'', b'hello, not a picture', jpeg()[:200]])
def test_a_body_that_is_not_a_jpeg_is_400(client, ready, body):
    r = post_picture(client, body)
    assert r.status_code == 400
    assert 'JPEG' in r.json()['detail']


def test_a_picture_over_the_size_limit_is_413(client, ready):
    assert post_picture(client, b'x' * (LIMIT + 1)).status_code == 413
    assert post_picture(client, b'x' * LIMIT).status_code == 400       # at the limit: read, then found broken


def test_an_unexpected_failure_while_detecting_is_500_with_the_reason(client, ready, yolo):
    def crash():
        raise RuntimeError('ncnn crashed')

    yolo.during_predict = crash
    r = post_picture(client, jpeg())
    assert r.status_code == 500
    assert 'ncnn crashed' in r.json()['detail']


def test_put_model_starts_loading_it(client, manager):
    r = client.put('/model', json=MODEL.to_dict())
    assert r.status_code == 200
    health = DetectorHealth.from_dict(r.json())
    assert health.state is ServiceState.LOADING and health.model is None
    manager.load_step()
    assert DetectorHealth.from_dict(client.get('/health').json()).model == MODEL


@pytest.mark.parametrize('body, word', [
    ('not json', 'JSON'),
    ('[1, 2]', 'JSON object'),
    ('{"model": "yolo11n.pt"}', 'format'),
    ('{"model": "yolo11n.pt", "format": "tflite", "image_size": 480, "confidence": 0.5}', 'format'),
    ('{"model": "yolo11n.onnx", "format": "ncnn", "image_size": 480, "confidence": 0.5}', '.pt'),
])
def test_put_model_with_bad_settings_is_400_and_says_what_is_wrong(client, manager, body, word):
    r = client.put('/model', content=body, headers={'Content-Type': 'application/json'})
    assert r.status_code == 400
    assert word in r.json()['detail']
    assert manager.health().state is ServiceState.STARTING


def test_while_running_it_loads_models_in_the_background(folder, manager):
    folder.remember(MODEL)
    with TestClient(create_app(manager, max_picture_bytes=LIMIT)) as client:
        give_up = time.monotonic() + 5
        while client.get('/health').json()['state'] != 'ready' and time.monotonic() < give_up:
            time.sleep(0.005)
        assert post_picture(client, jpeg()).status_code == 200
