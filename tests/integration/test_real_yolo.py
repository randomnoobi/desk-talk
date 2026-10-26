"""
The real detector (the model in config/settings.yaml) inside the whole app built by bootstrap: real pictures go
through the producer, and what the model finds is on the desk (only the objects on agents.can_join would join
the chat, and the sample pictures have none).

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
    picture = cv2.imread(str(ASSETS / name))          # comes with Ultralytics
    assert picture is not None
    return cv2.imencode('.jpg', picture)[1].tobytes()


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
    show(rig, 'zidane.jpg', 4)             # two people and a tie
    show(rig, 'bus.jpg', 4)                # a bus: not ignored, so it is on the desk, but it can't join the chat
    on_desk = {seen.label.lower() for seen in rig.app.scene.on_desk().values()}
    assert 'bus' in on_desk and 'person' not in on_desk
    assert rig.app.roster.member_ids() == []
    assert not any(m['type'] == 'roster' for m in rig.messages())
    assert rig.app.live_pictures.boxed(0) and rig.app.live_pictures.plain(0)     # the live view pictures
