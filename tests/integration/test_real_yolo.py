"""
The real detector (the model in config/settings.yaml) inside the whole app built by bootstrap: real pictures go
through the producer, and what the model finds joins the chat.

Skipped when the model file isn't in models/ (uv run python scripts/download_models.py).
"""

from __future__ import annotations

import pytest

from desktalk.adapters.detector import UltralyticsDetector, load_yolo
from tests.integration.app_kit import ROOT, DeskRig, changed, desk_config

pytestmark = pytest.mark.slow


def sample_jpeg(name: str) -> bytes:
    import cv2
    from ultralytics.utils import ASSETS
    return cv2.imencode('.jpg', cv2.imread(str(ASSETS / name)))[1].tobytes()   # comes with Ultralytics


@pytest.fixture
def rig(tmp_path):
    config = changed(desk_config(tmp_path), 'detector', device='cpu')
    model = ROOT / config.detector.model
    if not model.is_file():
        pytest.skip(f'{model.name} not downloaded (uv run python scripts/download_models.py)')
    s = config.detector
    detector = UltralyticsDetector(model, s, end2end=s.end2end, rect=s.rect, load_model=load_yolo)
    return DeskRig(tmp_path, config=config, detector=detector)


def show(rig: DeskRig, name: str, seconds: float) -> None:
    """The camera sends this sample picture for `seconds`, at the camera's rate."""
    jpeg = sample_jpeg(name)
    step = 1 / rig.config.camera.fps
    for _ in range(round(seconds / step)):
        rig.picture(jpeg=jpeg)
        rig.clock.advance(step)


def test_real_pictures_through_the_whole_app(rig):
    show(rig, 'zidane.jpg', 4)             # two people and a tie: nothing that should join
    show(rig, 'bus.jpg', 4)                # a bus: not ignored, so it joins as "Bus"
    names = {a['name'] for a in rig.app.roster.snapshot() if a['on_desk']}
    assert 'Bus' in names and 'Person' not in names and 'Tie' not in names
    assert any(m['type'] == 'roster' and m['agent']['name'] == 'Bus' for m in rig.messages())
    with rig.app.desk.lock:
        assert rig.app.desk.frames.get(0) and rig.app.desk.raw_frames.get(0)     # the live view pictures
