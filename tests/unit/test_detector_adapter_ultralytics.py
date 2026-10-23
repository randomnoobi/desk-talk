"""UltralyticsDetector: YOLO on this computer (phone, webcam, and backup for the Pi), with a fake YOLO
object instead of the real model. The real model runs in test_detector_adapter_real_model.py (slow)."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from desktalk.adapters.detector import UltralyticsDetector, pick_device
from desktalk.config import DetectorSettings
from desktalk.domain import BBox, Detection, DetectorUnavailable
from tests.contracts.detector_contract import DetectorContract

MODEL = Path('/models/yolo26m-objv1-150.pt')
CUP = ([10.0, 20.0, 50.0, 100.0], 0.83, 41.0)


class Col(list):
    """Stands in for a tensor column: has .tolist() like torch / numpy."""

    def tolist(self):
        return list(self)


class FakeYolo:
    """Stands in for ultralytics.YOLO: `names`, and predict() giving one result with the scripted boxes
    (xyxy, confidence, class number). It fails on the devices in `fail_on`, and records every call."""

    def __init__(self, boxes=(CUP,), names=None, fail_on=()):
        self.names = names or {0: 'person', 41: 'cup'}
        self.boxes = list(boxes)
        self.fail_on = set(fail_on)
        self.calls: list[dict] = []

    def predict(self, source, **options):
        self.calls.append({'shape': source.shape, **options})
        if options.get('device') in self.fail_on:
            raise RuntimeError(f'{options["device"]} ran out of memory')
        height, width = source.shape[:2]
        boxes = NS(xyxy=Col(b[0] for b in self.boxes), conf=Col(b[1] for b in self.boxes),
                   cls=Col(b[2] for b in self.boxes))
        return [NS(names=self.names, orig_shape=(height, width), boxes=boxes)]


def settings(**changes) -> DetectorSettings:
    return DetectorSettings(**{'model': 'models/yolo26m-objv1-150.pt', 'confidence': 0.35, 'image_size': 640,
                               'device': 'cpu', 'end2end': False, 'rect': False, **changes})


def detector_with(yolo, end2end=False, rect=False, **changes):
    return UltralyticsDetector(MODEL, settings(**changes), end2end=end2end, rect=rect, load_model=lambda path: yolo)


def a_picture():
    return np.zeros((480, 640, 3), np.uint8)


def cant_load(path):
    raise FileNotFoundError(f'{path} is not there')


class TestUltralyticsDetectorContract(DetectorContract):
    @pytest.fixture
    def detector(self):
        return detector_with(FakeYolo())

    @pytest.fixture
    def broken_detector(self):
        return UltralyticsDetector(MODEL, settings(), end2end=False, rect=False, load_model=cant_load)

    @pytest.fixture
    def picture(self):
        return a_picture()


def test_finds_the_models_words_and_boxes():
    found = detector_with(FakeYolo([CUP, ([0.0, 0.0, 700.0, 30.0], 0.5, 0.0)])).detect(a_picture())
    assert found == [Detection('cup', 0.83, BBox(10, 20, 50, 100)), Detection('person', 0.5, BBox(0, 0, 640, 30))]


def test_label_names_the_model_how_many_objects_it_knows_and_where_it_runs():
    assert detector_with(FakeYolo(), device='mps').label == 'yolo26m-objv1-150.pt (2 kinds of objects, mps)'


def test_runs_the_model_with_the_settings():
    yolo = FakeYolo()
    detector_with(yolo, confidence=0.4, image_size=480, device='cpu').detect(a_picture())
    assert yolo.calls == [{'shape': (480, 640, 3), 'conf': 0.4, 'imgsz': 480, 'device': 'cpu', 'verbose': False,
                           'end2end': False, 'rect': False}]


@pytest.mark.parametrize('end2end, rect', [(True, True), (False, True), (True, False)])
def test_end_to_end_head_and_padding_are_options(end2end, rect):
    """Decision 9: end2end=False, rect=False run the .pt the way the Pi's ncnn model runs."""
    yolo = FakeYolo()
    detector_with(yolo, end2end=end2end, rect=rect).detect(a_picture())
    assert (yolo.calls[0]['end2end'], yolo.calls[0]['rect']) == (end2end, rect)


# ── where it runs ────────────────────────────────────────────────────
def fake_torch(cuda=False, mps=False):
    return NS(cuda=NS(is_available=lambda: cuda), backends=NS(mps=NS(is_available=lambda: mps)))


@pytest.mark.parametrize('cuda, mps, device', [(True, True, 'cuda:0'), (False, True, 'mps'), (False, False, 'cpu')])
def test_auto_picks_the_nvidia_or_apple_gpu_when_there_is_one(cuda, mps, device):
    assert pick_device('auto', torch=fake_torch(cuda, mps)) == device


def test_a_chosen_device_is_used_as_written():
    assert pick_device('MPS', torch=fake_torch()) == 'mps'
    assert pick_device('cuda:1', torch=fake_torch()) == 'cuda:1'


def test_the_detector_uses_the_picked_device():
    yolo = FakeYolo()
    detector = detector_with(yolo, device='CPU')
    detector.detect(a_picture())
    assert yolo.calls[0]['device'] == 'cpu' and detector.label.endswith(', cpu)')


def test_falls_back_to_the_cpu_for_good_when_the_gpu_fails(caplog):
    yolo = FakeYolo(fail_on={'mps'})
    detector = detector_with(yolo, device='mps')
    assert detector.detect(a_picture())[0].label == 'cup'
    detector.detect(a_picture())
    assert [c['device'] for c in yolo.calls] == ['mps', 'cpu', 'cpu']
    assert detector.label.endswith(', cpu)')
    assert 'mps ran out of memory' in caplog.text and 'CPU' in caplog.text


# ── when it can't answer ─────────────────────────────────────────────
@pytest.mark.parametrize('device, fail_on', [('cpu', {'cpu'}), ('mps', {'mps', 'cpu'})])
def test_a_failing_model_is_detector_unavailable_with_the_reason(device, fail_on):
    detector = detector_with(FakeYolo(fail_on=fail_on), device=device)
    with pytest.raises(DetectorUnavailable, match='cpu ran out of memory'):
        detector.detect(a_picture())


def test_odd_results_are_detector_unavailable_not_a_crash():
    detector = detector_with(FakeYolo([([1.0, 1.0, 5.0, 5.0], 1.7, 41.0)]))      # confidence above 1
    with pytest.raises(DetectorUnavailable):
        detector.detect(a_picture())


def test_a_model_that_wont_load_says_so_and_is_not_tried_again_for_every_picture():
    tries = []

    def loader(path):
        tries.append(path)
        raise FileNotFoundError(f'{path} is not there')

    detector = UltralyticsDetector(MODEL, settings(), end2end=False, rect=False, load_model=loader)
    for _ in range(3):
        with pytest.raises(DetectorUnavailable, match='yolo26m-objv1-150.pt'):
            detector.detect(a_picture())
    assert tries == [MODEL]
    assert 'not loaded' in detector.label


# ── one at a time ────────────────────────────────────────────────────
class HeldYolo(FakeYolo):
    """A fake YOLO whose predict() waits until the test lets it go, counting how many run at once."""

    def __init__(self):
        super().__init__()
        self.running = 0
        self.most_at_once = 0
        self.started = threading.Event()
        self.let_go = threading.Event()

    def predict(self, source, **options):
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        self.started.set()
        self.let_go.wait(5)
        self.running -= 1
        return super().predict(source, **options)


def test_runs_one_detection_at_a_time():
    """Ultralytics builds its predictor on the first call; two threads at once break it."""
    yolo = HeldYolo()
    detector = detector_with(yolo)
    first = threading.Thread(target=detector.detect, args=(a_picture(),))
    second = threading.Thread(target=detector.detect, args=(a_picture(),))
    first.start()
    assert yolo.started.wait(5)
    second.start()
    second.join(0.1)                       # a moment for the second one to (wrongly) get in
    yolo.let_go.set()
    first.join(5)
    second.join(5)
    assert yolo.most_at_once == 1 and len(yolo.calls) == 2


# ── warming up at start ──────────────────────────────────────────────
def test_warm_up_runs_one_detection_on_a_blank_picture_of_the_models_size():
    yolo = FakeYolo()
    detector_with(yolo, image_size=480).warmup()
    assert [c['shape'] for c in yolo.calls] == [(480, 480, 3)]


def test_warm_up_of_a_detector_that_cant_answer_only_logs(caplog):
    detector = UltralyticsDetector(MODEL, settings(), end2end=False, rect=False, load_model=cant_load)
    caplog.clear()
    detector.warmup()
    assert 'warm' in caplog.text and 'yolo26m-objv1-150.pt' in caplog.text
