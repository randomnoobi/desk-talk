"""
The real detector (YOLO26 Objects365 model) on real pictures.
Skipped when the model file isn't in models/ (python scripts/download_models.py).
"""

import pytest

from conftest import Feeder
from desktalk.paths import MODELS_DIR

pytestmark = pytest.mark.slow
MODEL = MODELS_DIR / 'yolo26m-objv1-150.pt'


@pytest.fixture(scope='module')
def detector():
    if not MODEL.is_file():
        pytest.skip('model not downloaded (python scripts/download_models.py)')
    from desktalk.vision.detector import Detector
    return Detector(str(MODEL), confidence=0.35, device='cpu')


def picture(name):
    import cv2
    from ultralytics.utils import ASSETS
    return cv2.imread(str(ASSETS / name))


def test_detector_finds_things(detector):
    found = detector.detect(picture('bus.jpg'))
    labels = {o['label'] for o in found}
    assert {'person', 'bus'} <= labels
    o = found[0]
    assert set(o) == {'label', 'conf', 'bbox', 'center'} and 0 < o['conf'] <= 1
    assert len(detector.names) == 365


def test_real_pictures_through_the_whole_pipeline(make_app, detector):
    app = make_app(detector=detector)
    sse = app.generator.subscribe()
    f = Feeder(app)
    f.frame = picture('zidane.jpg')                # two people and a tie: nothing that should join
    f.run(4, None, person=False)
    f.frame = picture('bus.jpg')                   # a bus: not ignored, so it joins as "Bus"
    f.run(4, None, person=False)
    names = {a['name'] for a in app.roster.snapshot() if a['on_desk']}
    assert 'Bus' in names and 'Person' not in names and 'Tie' not in names
    msgs = []
    while not sse.empty():
        msgs.append(sse.get())
    assert any(m['type'] == 'roster' and m['agent']['name'] == 'Bus' for m in msgs)
    with app.desk.lock:
        assert app.desk.frames.get(0) and app.desk.raw_frames.get(0)     # live view pictures
