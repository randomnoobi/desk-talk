"""UltralyticsDetector with the real model from config/settings.yaml (slow).
Skipped when the model isn't in models/ (uv run python scripts/download_models.py).
Run with: uv run pytest -m slow tests/unit/test_detector_adapter_real_model.py"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from desktalk.adapters.detector import UltralyticsDetector, load_yolo
from desktalk.config import DetectorSettings
from tests.contracts.detector_contract import DetectorContract

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[2]


def detector_settings() -> DetectorSettings:
    data = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())['detector']
    return DetectorSettings.model_validate({**data, 'device': 'cpu'})


def sample(name: str):
    import cv2
    from ultralytics.utils import ASSETS
    return cv2.imread(str(ASSETS / name))          # comes with Ultralytics, so no network needed


@pytest.fixture(scope='module')
def real_detector():
    s = detector_settings()
    if not (ROOT / s.model).is_file():
        pytest.skip(f'{Path(s.model).name} not downloaded (uv run python scripts/download_models.py)')
    return UltralyticsDetector(ROOT / s.model, s, end2end=False, rect=False, load_model=load_yolo)


class TestRealModelContract(DetectorContract):
    @pytest.fixture
    def detector(self, real_detector):
        return real_detector

    @pytest.fixture
    def broken_detector(self, tmp_path):
        not_a_model = tmp_path / 'broken.pt'
        not_a_model.write_text('this is not a model')
        return UltralyticsDetector(not_a_model, detector_settings(), end2end=False, rect=False, load_model=load_yolo)

    @pytest.fixture
    def picture(self):
        return sample('bus.jpg')


def test_the_real_model_finds_the_bus_and_the_people(real_detector):
    labels = {d.label for d in real_detector.detect(sample('bus.jpg'))}
    assert {'person', 'bus'} <= labels
    assert '365 kinds of objects, cpu' in real_detector.label
