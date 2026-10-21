"""
The ncnn model (what the Pi will run) finds the same objects as the .pt model it was made from.

Ultralytics' ncnn export always leaves out YOLO26's end-to-end head and pads every picture to a square,
so here the .pt model is run the same way (end2end=False, rect=False). That checks the conversion itself.
How this differs from the app's normal .pt run: docs/plans/2026-10-21-ncnn-check.md.

Skipped unless both models are in models/ (model name and picture size from config/settings.yaml):
    uv run python scripts/download_models.py
    YOLO_AUTOINSTALL=false uv run yolo export model=models/yolo26m-objv1-150.pt format=ncnn imgsz=640
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

PICTURES = ('bus.jpg', 'zidane.jpg')     # come with Ultralytics, so no network needed
BOX_TOLERANCE = 3                        # pixels at the model's picture size, scaled up to each picture
NEAR_THRESHOLD = 0.1                     # boxes this close to the confidence threshold may come and go


@pytest.fixture(scope='module')
def detector_settings():
    from desktalk.settings import load_settings
    return load_settings(environ={}, dotenv=Path('/nonexistent')).detector


@pytest.fixture(scope='module')
def models(detector_settings):
    from desktalk.vision.detector import resolve_model_path
    pt = resolve_model_path(detector_settings.model)
    ncnn_dir = pt.with_name(f'{pt.stem}_ncnn_model')
    if not pt.is_file():
        pytest.skip(f'{pt.name} not downloaded (uv run python scripts/download_models.py)')
    if not ncnn_dir.is_dir():
        pytest.skip(f'{ncnn_dir.name} not made yet (YOLO_AUTOINSTALL=false uv run yolo export '
                    f'model=models/{pt.name} format=ncnn imgsz={detector_settings.image_size})')
    pytest.importorskip('ncnn')
    from ultralytics import YOLO
    return YOLO(str(pt)), YOLO(str(ncnn_dir), task='detect')


def detect(model, picture, s, **how):
    r = model.predict(source=picture, conf=s.confidence, imgsz=s.image_size, device='cpu', verbose=False, **how)[0]
    return [(r.names[int(c)], float(p), [float(v) for v in box])
            for box, c, p in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist(), strict=True)]


def corner_gap(a, b):
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def unmatched(found, other, strong_from, tolerance):
    """Strong boxes in `found` with no box of the same label in `other` within `tolerance` pixels."""
    missing = []
    for label, conf, box in found:
        if conf < strong_from:
            continue
        gaps = [corner_gap(box, b) for lab, _, b in other if lab == label]
        if not gaps or min(gaps) > tolerance:
            missing.append(f'{label} {conf:.2f} (closest {min(gaps):.1f} px)' if gaps else f'{label} {conf:.2f}')
    return missing


def test_ncnn_model_knows_the_same_objects(models):
    pt, ncnn = models
    assert dict(ncnn.names) == dict(pt.names)


@pytest.mark.parametrize('name', PICTURES)
def test_ncnn_finds_the_same_objects_as_pt(models, detector_settings, name):
    import cv2
    from ultralytics.utils import ASSETS
    picture = cv2.imread(str(ASSETS / name))
    pt, ncnn = models
    s = detector_settings
    from_pt = detect(pt, picture, s, end2end=False, rect=False)
    from_ncnn = detect(ncnn, picture, s)
    assert from_pt, f'the .pt model found nothing in {name}'
    tolerance = BOX_TOLERANCE * max(picture.shape[:2]) / s.image_size
    strong_from = s.confidence + NEAR_THRESHOLD
    only_pt = unmatched(from_pt, from_ncnn, strong_from, tolerance)
    only_ncnn = unmatched(from_ncnn, from_pt, strong_from, tolerance)
    assert not only_pt, f'{name}: .pt found these, ncnn did not (within {tolerance:.1f} px): {only_pt}'
    assert not only_ncnn, f'{name}: ncnn found these, .pt did not (within {tolerance:.1f} px): {only_ncnn}'
