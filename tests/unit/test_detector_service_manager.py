"""The detector service's model manager: which model answers, loading new ones, one detection at a time."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from desktalk_detector.manager import ModelManager
from desktalk_detector.model_folder import ModelFolder
from desktalk_shared.detection import DetectorUnavailable
from desktalk_shared.detector_api import ModelSettings, ServiceState
from tests.unit.detector_service_fakes import FakeYolo

MODEL = ModelSettings(model='yolo11n.pt', format='ncnn', image_size=480, confidence=0.5, half=False)
OTHER = ModelSettings(model='yolo11s.pt', format='pt', image_size=320, confidence=0.5, half=False)


class StepClock:
    """Each call is `step` seconds after the one before."""

    def __init__(self, step: float = 0.025):
        self.now, self.step = 100.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.fixture
def yolo():
    return FakeYolo([('cup', 0.9, (10, 20, 50, 60)), ('pen', 0.3, (0, 0, 4, 4))])


@pytest.fixture
def folder(tmp_path, yolo):
    for name in ('yolo11n.pt', 'yolo11s.pt', 'yolo26n.pt'):
        (tmp_path / name).write_bytes(b'weights')
    return ModelFolder(tmp_path, yolo)


@pytest.fixture
def manager(folder):
    return ModelManager(folder, retry_first_sec=5, retry_max_sec=40, clock=StepClock())


def picture(height=480, width=640):
    return np.zeros((height, width, 3), np.uint8)


def test_has_no_model_at_first_and_says_what_it_is_waiting_for(manager):
    health = manager.health()
    assert health.state is ServiceState.STARTING and health.model is None
    assert 'PUT /model' in health.message
    with pytest.raises(DetectorUnavailable, match='no model'):
        manager.detect(picture())


def test_loads_the_model_it_is_asked_for(manager):
    assert manager.want(MODEL).state is ServiceState.LOADING
    assert manager.load_step() is None
    health = manager.health()
    assert health.state is ServiceState.READY and health.model == MODEL


def test_detect_gives_what_the_model_finds_above_its_confidence(manager):
    manager.want(MODEL)
    manager.load_step()
    reply = manager.detect(picture(height=480, width=640))
    assert [d.to_dict() for d in reply.detections] == [
        {'label': 'cup', 'conf': 0.9, 'bbox': [10.0, 20.0, 50.0, 60.0], 'center': [30.0, 40.0]}]
    assert (reply.width, reply.height, reply.model) == (640, 480, 'yolo11n.pt (ncnn, 480)')


def test_detect_runs_the_model_at_its_picture_size_on_the_cpu(manager, yolo):
    manager.want(MODEL)
    manager.load_step()
    manager.detect(picture())
    assert (yolo.predictions[-1]['imgsz'], yolo.predictions[-1]['device']) == (480, 'cpu')


def test_detection_time_is_measured_and_counted(manager):
    manager.want(MODEL)
    manager.load_step()
    assert manager.detect(picture()).detect_ms == pytest.approx(25.0)    # the clock moves 25 ms per call
    manager.detect(picture())
    health = manager.health()
    assert health.detections_done == 2 and health.last_detect_ms == pytest.approx(25.0)


def test_a_new_model_is_warmed_up_before_it_answers(manager, yolo):
    """Ultralytics sets itself up on the first picture, which is slow; that shouldn't be the camera's picture."""
    manager.want(MODEL)
    manager.load_step()
    assert [p['shape'] for p in yolo.predictions] == [(480, 480, 3)]


def test_a_failed_load_says_why_in_plain_words(manager, yolo):
    yolo.fail_open = 'the file is damaged'
    manager.want(MODEL)
    assert manager.load_step() == 5
    health = manager.health()
    assert health.state is ServiceState.ERROR and health.model is None
    assert 'yolo11n.pt' in health.message and 'the file is damaged' in health.message and '5 s' in health.message
    with pytest.raises(DetectorUnavailable, match='the file is damaged'):
        manager.detect(picture())


def test_a_failed_load_is_tried_again_with_longer_and_longer_waits(manager, yolo):
    yolo.fail_open = 'the file is damaged'
    manager.want(MODEL)
    assert [manager.load_step() for _ in range(6)] == [5, 10, 20, 40, 40, 40]
    yolo.fail_open = None
    assert manager.load_step() is None
    assert manager.health().state is ServiceState.READY


def test_the_last_good_model_answers_while_a_new_one_loads(manager):
    manager.want(MODEL)
    manager.load_step()
    health = manager.want(OTHER)
    assert health.state is ServiceState.LOADING and health.model == MODEL
    assert manager.detect(picture()).model == 'yolo11n.pt (ncnn, 480)'
    manager.load_step()
    assert manager.detect(picture()).model == 'yolo11s.pt (pt, 320)'


def test_the_last_good_model_keeps_answering_when_a_new_one_fails(manager, yolo):
    manager.want(MODEL)
    manager.load_step()
    yolo.fail_open = 'out of memory'
    manager.want(OTHER)
    manager.load_step()
    health = manager.health()
    assert health.state is ServiceState.LOADING and health.model == MODEL
    assert 'out of memory' in health.message and 'yolo11n.pt' in health.message
    assert manager.detect(picture()).model == 'yolo11n.pt (ncnn, 480)'


def test_a_newer_request_during_a_load_wins(manager, yolo):
    yolo.during_predict = lambda: manager.want(OTHER)     # asked for OTHER while MODEL is warming up
    manager.want(MODEL)
    assert manager.load_step() == 0                        # MODEL is dropped; load OTHER straight away
    yolo.during_predict = None
    assert manager.load_step() is None
    assert manager.health().model == OTHER


def test_asking_for_the_model_that_is_running_loads_nothing(manager, yolo):
    manager.want(MODEL)
    manager.load_step()
    opened = len(yolo.opened)
    assert manager.want(MODEL).state is ServiceState.READY
    assert manager.load_step() is None
    assert len(yolo.opened) == opened


def test_asking_for_the_running_model_cancels_a_load_that_keeps_failing(manager, yolo):
    manager.want(MODEL)
    manager.load_step()
    yolo.fail_open = 'no such file'
    manager.want(OTHER)
    manager.load_step()
    health = manager.want(MODEL)
    assert health.state is ServiceState.READY and 'no such file' not in health.message


def test_a_new_request_starts_the_waits_short_again(manager, yolo):
    yolo.fail_open = 'no such file'
    manager.want(MODEL)
    manager.load_step(), manager.load_step(), manager.load_step()
    manager.want(OTHER)
    assert manager.load_step() == 5


def test_a_newer_request_is_not_blamed_for_the_older_model_failing(manager, yolo):
    def ask_for_other_then_break():
        yolo.during_predict = None
        manager.want(OTHER)
        raise RuntimeError('ncnn crashed')

    yolo.during_predict = ask_for_other_then_break
    manager.want(MODEL)
    assert manager.load_step() == 0
    assert 'ncnn crashed' not in manager.health().message
    assert manager.load_step() is None and manager.health().model == OTHER


def test_remembers_the_model_it_was_asked_for(manager, folder):
    manager.want(OTHER)
    assert folder.remembered() == OTHER


def test_a_model_that_is_not_a_pt_file_is_turned_down_and_changes_nothing(manager, folder):
    with pytest.raises(ValueError, match=r'\.pt'):
        manager.want(ModelSettings(model='yolo11n.onnx', format='ncnn', image_size=480, confidence=0.5, half=False))
    assert manager.health().state is ServiceState.STARTING
    assert folder.remembered() is None


def test_says_how_a_yolo26_model_runs_differently_as_ncnn(manager):
    manager.want(ModelSettings(model='yolo26n.pt', format='ncnn', image_size=480, confidence=0.5, half=False))
    manager.load_step()
    assert 'end-to-end' in manager.health().message


@pytest.mark.parametrize('settings', [MODEL, ModelSettings(model='yolo26n.pt', format='pt', image_size=480,
                                                           confidence=0.5, half=False)])
def test_other_models_need_no_such_note(manager, settings):
    manager.want(settings)
    manager.load_step()
    assert 'end-to-end' not in manager.health().message


def test_runs_one_detection_at_a_time(manager, yolo):
    """Ultralytics' predictor breaks when two threads use it at once (FastAPI answers requests in threads)."""
    manager.want(MODEL)
    manager.load_step()
    first_inside, release, second_inside = threading.Event(), threading.Event(), threading.Event()

    def hold_the_first_one():
        if not first_inside.is_set():
            first_inside.set()
            release.wait(5)
        else:
            second_inside.set()

    yolo.during_predict = hold_the_first_one
    first = threading.Thread(target=manager.detect, args=(picture(),))
    first.start()
    assert first_inside.wait(5)
    second = threading.Thread(target=manager.detect, args=(picture(),))
    second.start()
    assert not second_inside.wait(0.05)        # the second picture waits for the first
    release.set()
    first.join(5), second.join(5)
    assert second_inside.is_set() and manager.health().detections_done == 2


def wait_for(condition, seconds=5.0) -> bool:
    """For the background-thread tests only: True as soon as condition() is (normally within milliseconds)."""
    give_up = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > give_up:
            return False
        time.sleep(0.005)
    return True


def loader_threads():
    return [t for t in threading.enumerate() if t.name == 'model loader']


def test_once_started_it_loads_in_the_background_beginning_with_the_remembered_model(folder):
    folder.remember(MODEL)
    manager = ModelManager(folder, retry_first_sec=5, retry_max_sec=40)
    manager.start()
    try:
        assert wait_for(lambda: manager.health().state is ServiceState.READY)
        assert manager.health().model == MODEL
        manager.want(OTHER)
        assert wait_for(lambda: manager.health().model == OTHER)
    finally:
        manager.stop()
    assert wait_for(lambda: not loader_threads())


def test_starting_with_nothing_remembered_waits_to_be_asked(folder):
    manager = ModelManager(folder, retry_first_sec=5, retry_max_sec=40)
    manager.start()
    try:
        assert manager.health().state is ServiceState.STARTING
    finally:
        manager.stop()
    assert wait_for(lambda: not loader_threads())


def test_a_models_folder_it_cannot_write_to_does_not_stop_a_model_loading(folder, tmp_path):
    """e.g. a Docker volume owned by another user: it can't remember the model, but can still run it."""
    manager = ModelManager(folder, retry_first_sec=5, retry_max_sec=40)
    tmp_path.chmod(0o555)
    try:
        manager.want(OTHER)
        assert manager.load_step() is None
    finally:
        tmp_path.chmod(0o755)
    assert manager.health().model == OTHER
