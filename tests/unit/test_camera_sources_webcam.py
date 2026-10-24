"""A webcam on this computer (WebcamSource), with a fake OpenCV capture instead of a real camera."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from desktalk.adapters.camera import WebcamSource, open_webcam
from desktalk.config import CameraSettings
from desktalk.ports import FrameSource
from tests.contracts.frame_source_contract import FrameSourceContract
from tests.fakes import FakeClock

ROOT = Path(__file__).resolve().parents[2]


def camera_settings(**changes) -> CameraSettings:
    """The camera section of config/settings.yaml, with some values changed for a test."""
    data = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())['camera']
    return CameraSettings.model_validate({**data, **changes})


def picture(value: int):
    return np.full((48, 64, 3), value, np.uint8)


class FakeCapture:
    """Stands in for cv2.VideoCapture: read() hands out the queued pictures, then (False, None)."""

    def __init__(self, *pictures):
        self.pictures = list(pictures)
        self.settings: dict[int, float] = {}
        self.released = False

    def read(self):
        if not self.pictures:
            return False, None
        item = self.pictures.pop(0)
        return (item is not None), item

    def set(self, prop, value):
        self.settings[prop] = value
        return True

    def release(self):
        self.released = True


class Opener:
    """Opens the fake webcams by number, and remembers which numbers were tried."""

    def __init__(self, cams: dict[int, FakeCapture]):
        self.cams = cams
        self.tried: list[int] = []

    def __call__(self, index: int):
        self.tried.append(index)
        return self.cams.get(index)


class TestWebcamSourceContract(FrameSourceContract):
    @pytest.fixture
    def cam(self):
        return FakeCapture()

    @pytest.fixture
    def source(self, cam):
        return WebcamSource(FakeClock(), camera_settings(), index=0, open_capture=Opener({0: cam}))

    @pytest.fixture
    def send(self, cam):
        return lambda source, picture: cam.pictures.append(picture)


@pytest.fixture
def clock():
    return FakeClock()


def webcam(clock, cams, index=0, **changes):
    opener = Opener(cams)
    return WebcamSource(clock, camera_settings(**changes), index=index, open_capture=opener), opener


def test_the_chosen_webcam_is_tried_first_then_the_others(clock):
    empty, good = FakeCapture(), FakeCapture(picture(5))
    source, opener = webcam(clock, {0: empty, 2: good}, index=1, webcam_max_index=4)
    frame = source.read(timeout=1)
    assert frame is not None
    assert opener.tried == [1, 0, 2]          # 1 doesn't open, 0 gives no pictures, 2 works
    assert source.index == 2 and int(frame.picture[0, 0, 0]) == 5
    assert empty.released and not good.released


def test_asks_the_webcam_for_the_picture_size_and_a_short_queue(clock):
    cam = FakeCapture(picture(5))
    source, _ = webcam(clock, {0: cam}, webcam_width=960, webcam_height=540)
    source.read(timeout=1)
    assert cam.settings == {cv2.CAP_PROP_FRAME_WIDTH: 960, cv2.CAP_PROP_FRAME_HEIGHT: 540,
                            cv2.CAP_PROP_BUFFERSIZE: 1}


@pytest.mark.parametrize('reads, works', [(2, False), (3, True)])
def test_a_webcam_must_give_a_good_picture_within_the_warm_up_reads(clock, reads, works):
    cam = FakeCapture(None, np.zeros((0, 0, 3), np.uint8), picture(5))     # two bad pictures, then a good one
    source, _ = webcam(clock, {0: cam}, webcam_warmup_reads=reads, webcam_max_index=1)
    assert (source.read(timeout=1) is not None) is works


def test_with_no_working_webcam_read_waits_and_gives_nothing(clock):
    source, opener = webcam(clock, {}, webcam_max_index=2)
    assert source.read(timeout=0.7) is None
    assert source.read(timeout=0.7) is None
    assert clock.slept == [0.7, 0.7]
    assert opener.tried == [0, 1]             # gave up after trying them all once, as before
    assert not source.connected


def test_when_the_webcam_stops_giving_pictures_read_waits(clock):
    source, _ = webcam(clock, {0: FakeCapture(picture(5))})
    assert source.read(timeout=0.5) is not None
    assert source.read(timeout=0.5) is None
    assert clock.slept == [0.5]


def test_not_connected_once_pictures_stop_for_the_disconnect_time(clock):
    source, _ = webcam(clock, {0: FakeCapture(picture(5))}, disconnect_after_sec=3)
    source.read(timeout=0.1)
    clock.advance(2.9)
    assert source.connected
    clock.advance(0.2)
    assert not source.connected


def test_speed_is_measured_from_the_pictures_read(clock):
    cam = FakeCapture(picture(1))
    source, _ = webcam(clock, {0: cam}, fps_smoothing=0.2)
    source.read(timeout=0.1)
    clock.advance(0.25)
    cam.pictures.append(picture(2))
    source.read(timeout=0.1)
    assert source.fps == 4.0


def test_release_closes_the_webcam(clock):
    cam = FakeCapture(picture(5))
    source, _ = webcam(clock, {0: cam})
    source.read(timeout=0.1)
    source.release()
    assert cam.released


# ── opening a real webcam with OpenCV ─────────────────────────────────
class FakeVideoCapture:
    """Stands in for cv2.VideoCapture: opens only with the backends listed in `works`."""

    works: set = set()
    made: list = []

    def __init__(self, index, backend=None):
        self.index, self.backend, self.released = index, backend, False
        FakeVideoCapture.made.append(self)

    def isOpened(self):
        return self.backend in FakeVideoCapture.works

    def release(self):
        self.released = True


@pytest.fixture
def fake_opencv(monkeypatch):
    monkeypatch.setattr(cv2, 'VideoCapture', FakeVideoCapture)
    FakeVideoCapture.made = []
    return FakeVideoCapture


def test_opens_a_webcam_the_default_way_first(fake_opencv):
    fake_opencv.works = {None}
    cap = open_webcam(1, platform='darwin')
    assert cap is fake_opencv.made[0] and cap.index == 1 and len(fake_opencv.made) == 1


def test_on_a_mac_tries_avfoundation_when_the_default_way_fails(fake_opencv):
    fake_opencv.works = {cv2.CAP_AVFOUNDATION}
    cap = open_webcam(0, platform='darwin')
    assert cap.backend == cv2.CAP_AVFOUNDATION
    assert fake_opencv.made[0].released


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
def test_a_webcam_that_wont_open_gives_none(fake_opencv, platform):
    fake_opencv.works = set()
    assert open_webcam(0, platform=platform) is None
    assert all(c.released for c in fake_opencv.made)
    assert len(fake_opencv.made) == (2 if platform == 'darwin' else 1)


def test_webcam_has_all_of_its_interface(clock):
    source, _ = webcam(clock, {})
    assert isinstance(source, FrameSource)
