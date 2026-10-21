"""Cameras that send us pictures: the Pi (PiFrameSource) and the phone (PhoneFrameSource)."""

from __future__ import annotations

import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from desktalk.adapters.camera import PhoneFrameSource, PiFrameSource
from desktalk.config import CameraSettings
from desktalk.domain import BBox, Detection, DetectorState, PiHealth, UploadInfo
from desktalk.ports import FrameSource, PictureInbox
from desktalk_shared.wire import info_from_v1_headers
from tests.contracts.frame_source_contract import FrameSourceContract
from tests.contracts.push_source_contract import PushSourceContract
from tests.fakes import FakeClock

ROOT = Path(__file__).resolve().parents[2]


def camera_settings(**changes) -> CameraSettings:
    """The camera section of config/settings.yaml, with some values changed for a test."""
    data = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())['camera']
    return CameraSettings.model_validate({**data, **changes})


def jpeg_of(picture) -> bytes:
    return cv2.imencode('.jpg', picture)[1].tobytes()


class TestPiFrameSourceContract(FrameSourceContract, PushSourceContract):
    @pytest.fixture
    def source(self):
        return PiFrameSource(FakeClock(), camera_settings())

    @pytest.fixture
    def send(self):
        return lambda source, picture: source.push(jpeg_of(picture))

    @pytest.fixture(name='jpeg_of')
    def jpeg_maker(self):
        return jpeg_of


class TestPhoneFrameSourceContract(FrameSourceContract, PushSourceContract):
    @pytest.fixture
    def source(self):
        return PhoneFrameSource(FakeClock(), camera_settings())

    @pytest.fixture
    def send(self):
        return lambda source, picture: source.push(jpeg_of(picture))

    @pytest.fixture(name='jpeg_of')
    def jpeg_maker(self):
        return jpeg_of


def picture(value: int, width: int = 64, height: int = 48):
    return np.full((height, width, 3), value, np.uint8)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture(params=[PiFrameSource, PhoneFrameSource], ids=['pi', 'phone'])
def make_source(request, clock):
    return lambda **changes: request.param(clock, camera_settings(**changes))


def test_an_empty_upload_is_refused_without_crashing(make_source):
    source = make_source()
    assert source.push(b'') is False
    assert not source.connected


def test_not_connected_once_pictures_stop_for_the_disconnect_time(make_source, clock):
    source = make_source(disconnect_after_sec=3)
    source.push(jpeg_of(picture(50)))
    clock.advance(2.9)
    assert source.connected
    clock.advance(0.2)
    assert not source.connected


def test_speed_is_pictures_a_second_smoothed(make_source, clock):
    source = make_source(fps_smoothing=0.2)
    source.push(jpeg_of(picture(1)))
    clock.advance(0.5)
    source.push(jpeg_of(picture(2)))
    assert source.fps == 2.0                    # the first gap is taken as it is
    clock.advance(0.25)
    source.push(jpeg_of(picture(3)))
    assert source.fps == 2.4                    # 0.8 x 2 + 0.2 x 4


def test_a_long_gap_does_not_count_toward_the_speed(make_source, clock):
    source = make_source(fps_gap_max_sec=1.5, disconnect_after_sec=3)
    source.push(jpeg_of(picture(1)))
    clock.advance(0.5)
    source.push(jpeg_of(picture(2)))
    clock.advance(2.0)
    source.push(jpeg_of(picture(3)))
    assert source.fps == 2.0


def test_speed_is_zero_when_not_connected(make_source, clock):
    source = make_source(disconnect_after_sec=3)
    source.push(jpeg_of(picture(1)))
    clock.advance(0.5)
    source.push(jpeg_of(picture(2)))
    clock.advance(3)
    assert source.fps == 0.0


def test_read_waits_for_a_picture_that_arrives_meanwhile(make_source):
    source = make_source()
    upload = threading.Timer(0.05, source.push, args=(jpeg_of(picture(99)),))
    upload.start()
    try:
        frame = source.read(timeout=5)
    finally:
        upload.join()
    assert frame is not None and int(frame.picture[0, 0, 0]) > 90


def test_pictures_carry_this_computers_time(make_source, clock):
    source = make_source()
    clock.advance(12.5)
    source.push(jpeg_of(picture(1)))
    assert source.read(timeout=0.5).received_at == 1_760_000_012.5


def test_counts_the_pictures_received(make_source):
    source = make_source()
    source.push(jpeg_of(picture(1)))
    source.push(b'not a picture')
    source.push(jpeg_of(picture(2)))
    assert source.pictures_received == 2


def test_stop_pressed_means_not_connected_until_the_next_picture(make_source):
    source = make_source()
    source.push(jpeg_of(picture(1)))
    source.mark_stopped()
    assert source.stopped_by_user and not source.connected
    source.push(jpeg_of(picture(2)))
    assert not source.stopped_by_user and source.connected


# ── the Pi ───────────────────────────────────────────────────────────
OLD_PI_HEADERS = {'X-Frame-Id': 'pi-000042', 'X-Taken-At': '1760000000.250', 'X-Pi-Camera': 'picamera2',
                  'X-Pi-Photos': '42', 'X-Pi-Free-MB': '1234', 'X-Pi-Last-Cleanup': '1759999000', 'X-Lux': '312.5'}


def pi_source(clock, **changes):
    return PiFrameSource(clock, camera_settings(**changes))


def test_a_pi_picture_carries_what_the_pi_sent(clock):
    source = pi_source(clock)
    cup = Detection('cup', 0.8, BBox(1, 2, 30, 40))
    info = UploadInfo(frame_id='pi-000007', taken_at=1_760_000_000.0, lux=200.0, detector=DetectorState.OK,
                      detections=(cup,), detect_ms=240.0, model='yolo26m-objv1-150_ncnn_model')
    source.push(jpeg_of(picture(10)), info)
    frame = source.read(timeout=0.5)
    assert frame.pi == info
    assert frame.detector_state is DetectorState.OK and frame.pi_detections == (cup,)
    assert frame.frame_id == 'pi-000007'


def test_pi_info_before_any_picture_has_only_the_frame_id():
    assert pi_source(FakeClock()).pi_info() == {'last_frame_id': ''}


def test_pi_info_keeps_what_the_pi_said_about_itself(clock):
    source = pi_source(clock)
    source.push(jpeg_of(picture(10)), info_from_v1_headers(OLD_PI_HEADERS))
    assert source.pi_info() == {'last_frame_id': 'pi-000042', 'camera': 'picamera2', 'photos_stored': 42,
                                'disk_free_mb': 1234, 'last_cleanup_ts': 1_759_999_000.0}
    # A USB camera on the Pi leaves the counts blank: the last known ones stay.
    usb = {**OLD_PI_HEADERS, 'X-Frame-Id': 'pi-000043', 'X-Pi-Camera': 'usb', 'X-Pi-Photos': '', 'X-Pi-Free-MB': ''}
    source.push(jpeg_of(picture(10)), info_from_v1_headers(usb))
    assert source.pi_info() == {'last_frame_id': 'pi-000043', 'camera': 'usb', 'photos_stored': 42,
                                'disk_free_mb': 1234, 'last_cleanup_ts': 1_759_999_000.0}


def test_pi_info_includes_temperature_and_slow_down_flags_when_sent(clock):
    source = pi_source(clock)
    info = UploadInfo(frame_id='pi-000001', taken_at=None,
                      health=PiHealth(camera='picamera2', cpu_temp_c=71.5, throttled='0x50005'))
    source.push(jpeg_of(picture(10)), info)
    assert source.pi_info() == {'last_frame_id': 'pi-000001', 'camera': 'picamera2', 'cpu_temp_c': 71.5,
                                'throttled': '0x50005'}


def test_status_says_why_detection_on_the_pi_is_off(clock):
    source = pi_source(clock)
    off = UploadInfo(frame_id='pi-000003', taken_at=None, detector=DetectorState.OFF,
                     detector_reason='the detector container is not answering')
    source.push(jpeg_of(picture(10)), off)
    status = source.status()
    assert status['detector'] == 'off' and status['detector_reason'] == 'the detector container is not answering'
    assert status['upload_format'] == 2 and status['last_frame_id'] == 'pi-000003'


def test_status_keeps_the_last_detection_time_and_model(clock):
    source = pi_source(clock)
    ok = UploadInfo(frame_id='pi-000001', taken_at=None, detector=DetectorState.OK, detect_ms=240.0,
                    model='yolo26m-objv1-150_ncnn_model')
    busy = UploadInfo(frame_id='pi-000002', taken_at=None, detector=DetectorState.BUSY)
    source.push(jpeg_of(picture(10)), ok)
    source.push(jpeg_of(picture(10)), busy)
    status = source.status()
    assert status['detector'] == 'busy'
    assert status['detect_ms'] == 240.0 and status['model'] == 'yolo26m-objv1-150_ncnn_model'


def test_status_tells_how_far_the_pis_clock_is_off(clock):
    source = pi_source(clock)
    behind = UploadInfo(frame_id='pi-000001', taken_at=clock.now() - 6.0)
    source.push(jpeg_of(picture(10)), behind)
    assert source.status()['clock_off_sec'] == -6.0
    source.push(jpeg_of(picture(10)), UploadInfo(frame_id='pi-000002', taken_at=None))
    assert source.status()['clock_off_sec'] == -6.0          # a picture without a time doesn't change it


def test_status_before_any_picture():
    status = pi_source(FakeClock()).status()
    assert status == {'last_frame_id': '', 'upload_format': None, 'detector': None, 'detector_reason': '',
                      'detect_ms': None, 'model': '', 'clock_off_sec': None}


# ── the phone ────────────────────────────────────────────────────────
def phone_source(clock, **changes):
    return PhoneFrameSource(clock, camera_settings(**changes))


def test_phone_tilt_is_used_only_while_it_is_recent(clock):
    source = phone_source(clock, phone_tilt_max_age_sec=5)
    assert source.recent_gravity() is None
    source.set_motion(gravity=(0.12, -9.71, 0.85))
    clock.advance(4.9)
    assert source.recent_gravity() == (0.12, -9.71, 0.85)
    clock.advance(0.2)
    assert source.recent_gravity() is None


def test_phone_rotation_and_tilt_are_kept_when_the_other_one_comes(clock):
    source = phone_source(clock)
    assert source.rotation == 0
    source.set_motion(gravity=(0.0, -9.8, 0.0), rotation=90)
    source.set_motion(rotation=180)
    assert source.recent_gravity() == (0.0, -9.8, 0.0)
    source.set_motion(gravity=(1.0, -9.7, 0.0))
    assert source.rotation == 180


def test_pi_and_phone_have_all_of_their_interfaces(make_source):
    source = make_source()
    assert isinstance(source, FrameSource) and isinstance(source, PictureInbox)
