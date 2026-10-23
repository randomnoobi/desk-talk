"""Starting the detector service: settings from the environment → the parts → uvicorn."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from desktalk_detector import __main__ as service_main
from desktalk_detector.bootstrap import build_app
from desktalk_detector.settings import ServiceSettings
from desktalk_shared.detector_api import ModelSettings
from tests.unit.detector_service_fakes import FakeYolo

MODEL = ModelSettings(model='yolo11n.pt', format='pt', image_size=320, confidence=0.35, half=False)


def service_settings(models_dir: Path, max_picture_bytes: int = 100_000) -> ServiceSettings:
    return ServiceSettings(host='127.0.0.1', port=8765, models_dir=models_dir, max_picture_bytes=max_picture_bytes,
                           retry_first_sec=5, retry_max_sec=40)


def test_the_built_app_loads_models_from_the_models_folder_in_the_settings(tmp_path):
    (tmp_path / 'yolo11n.pt').write_bytes(b'weights')
    app = build_app(service_settings(tmp_path), yolo=FakeYolo([('cup', 0.9, (1, 1, 5, 5))]))
    with TestClient(app) as client:
        assert client.put('/model', json=MODEL.to_dict()).status_code == 200
        give_up = time.monotonic() + 5
        while client.get('/health').json()['state'] != 'ready' and time.monotonic() < give_up:
            time.sleep(0.005)
        jpeg = cv2.imencode('.jpg', np.zeros((48, 64, 3), np.uint8))[1].tobytes()
        assert client.post('/detect', content=jpeg).json()['model'] == 'yolo11n.pt (pt, 320)'
    assert (tmp_path / 'last_model.json').is_file()


def test_the_built_app_uses_the_picture_size_limit_in_the_settings(tmp_path):
    client = TestClient(build_app(service_settings(tmp_path, max_picture_bytes=1000), yolo=FakeYolo()))
    assert client.post('/detect', content=b'x' * 1001).status_code == 413


def test_main_runs_the_web_server_where_the_settings_say_without_a_line_per_request(monkeypatch, tmp_path):
    """3 pictures a second would otherwise fill the Pi's SD card with log lines."""
    ran = []
    monkeypatch.setattr(service_main.uvicorn, 'run', lambda app, **where: ran.append(where))
    env = {'DETECTOR_HOST': '127.0.0.1', 'DETECTOR_PORT': '8799', 'DETECTOR_MODELS_DIR': str(tmp_path),
           'DETECTOR_MAX_PICTURE_MB': '10', 'DETECTOR_RETRY_FIRST_SEC': '5', 'DETECTOR_RETRY_MAX_SEC': '300'}
    assert service_main.main(env) == 0
    assert ran == [{'host': '127.0.0.1', 'port': 8799, 'access_log': False}]


def test_main_stops_with_a_clear_message_when_a_setting_is_missing(monkeypatch, caplog):
    monkeypatch.setattr(service_main.uvicorn, 'run', lambda app, **where: None)
    with caplog.at_level(logging.ERROR):
        assert service_main.main({}) != 0
    assert 'DETECTOR_' in caplog.text
