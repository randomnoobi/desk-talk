"""
The detector service with the real Ultralytics: it converts yolo11n.pt to ncnn the first time, then finds
things in a photo through its HTTP API, and passes the Detector contract.

Skipped unless models/yolo11n.pt is there:
    uv run python scripts/download_models.py yolo11n.pt
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from desktalk_detector.app import create_app
from desktalk_detector.manager import ModelManager
from desktalk_detector.model_folder import ModelFolder
from desktalk_shared.detector_api import ModelSettings, ServiceState
from tests.contracts.detector_contract import DetectorContract
from tests.unit.detector_service_fakes import InProcessServiceDetector

pytestmark = pytest.mark.slow

PT = Path(__file__).resolve().parents[2] / 'models' / 'yolo11n.pt'
MODEL = ModelSettings(model='yolo11n.pt', format='ncnn', image_size=320, confidence=0.35, half=False)


def new_manager(models_dir: Path) -> ModelManager:
    return ModelManager(ModelFolder(models_dir), retry_first_sec=5, retry_max_sec=40)


@pytest.fixture(scope='module')
def models_dir(tmp_path_factory):
    if not PT.is_file():
        pytest.skip(f'{PT.name} not downloaded (uv run python scripts/download_models.py {PT.name})')
    pytest.importorskip('ncnn')
    pytest.importorskip('pnnx')
    folder = tmp_path_factory.mktemp('models')
    shutil.copy(PT, folder / PT.name)
    return folder


@pytest.fixture(scope='module')
def loaded(models_dir):
    manager = new_manager(models_dir)
    manager.want(MODEL)
    assert manager.load_step() is None, manager.health().message
    return manager


@pytest.fixture(scope='module')
def bus():
    import cv2
    from ultralytics.utils import ASSETS
    return cv2.imread(str(ASSETS / 'bus.jpg'))      # comes with Ultralytics, so no network needed


def test_converts_the_pt_to_ncnn_and_runs_it(loaded, models_dir):
    assert (models_dir / 'yolo11n_320_fp32_ncnn_model' / 'metadata.yaml').is_file()
    assert loaded.health().state is ServiceState.READY


def test_a_second_start_uses_the_ncnn_model_already_made(loaded, models_dir):
    made = (models_dir / 'yolo11n_320_fp32_ncnn_model' / 'model.ncnn.bin').stat().st_mtime_ns
    again = new_manager(models_dir)
    again.want(MODEL)
    assert again.load_step() is None
    assert (models_dir / 'yolo11n_320_fp32_ncnn_model' / 'model.ncnn.bin').stat().st_mtime_ns == made


def test_finds_the_bus_and_the_people_in_ultralytics_sample_photo(loaded, bus):
    labels = {d.label for d in loaded.detect(bus).detections}
    assert {'bus', 'person'} <= labels


class TestRealDetectorServiceInProcess(DetectorContract):
    @pytest.fixture
    def detector(self, loaded):
        return InProcessServiceDetector(TestClient(create_app(loaded, max_picture_bytes=10_000_000)))

    @pytest.fixture
    def broken_detector(self, tmp_path):
        return InProcessServiceDetector(TestClient(create_app(new_manager(tmp_path), max_picture_bytes=10_000_000)))

    @pytest.fixture
    def picture(self, bus):
        return np.ascontiguousarray(bus)
