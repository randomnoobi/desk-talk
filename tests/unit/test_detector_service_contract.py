"""The detector service, seen through its HTTP API as a desktalk Detector, passes the Detector contract."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from desktalk_detector.app import create_app
from desktalk_detector.manager import ModelManager
from desktalk_detector.model_folder import ModelFolder
from desktalk_shared.detector_api import ModelSettings
from tests.contracts.detector_contract import DetectorContract
from tests.unit.detector_service_fakes import FakeYolo, InProcessServiceDetector

MODEL = ModelSettings(model='yolo11n.pt', format='ncnn', image_size=320, confidence=0.35, half=False)


def service(tmp_path, *, load: bool) -> InProcessServiceDetector:
    (tmp_path / 'yolo11n.pt').write_bytes(b'weights')
    manager = ModelManager(ModelFolder(tmp_path, FakeYolo([('cup', 0.9, (10, 20, 50, 60))])),
                           retry_first_sec=5, retry_max_sec=40)
    if load:
        manager.want(MODEL)
        manager.load_step()
    return InProcessServiceDetector(TestClient(create_app(manager, max_picture_bytes=1_000_000)))


class TestDetectorServiceInProcess(DetectorContract):
    @pytest.fixture
    def detector(self, tmp_path):
        return service(tmp_path, load=True)

    @pytest.fixture
    def broken_detector(self, tmp_path):
        return service(tmp_path, load=False)       # no model yet: 503

    @pytest.fixture
    def picture(self):
        return np.zeros((120, 160, 3), np.uint8)
