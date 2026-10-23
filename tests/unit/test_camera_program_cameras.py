"""raspberry_pi/camera/cameras.py: the Pi camera module, a USB webcam, a folder of pictures or a video."""

import sys
import types

import cv2
import numpy as np
import pytest
from test_camera_program_fakes import FakeClock

from raspberry_pi.camera.cameras import CameraError, FolderCamera, open_camera


def open_kind(kind, clock=None):
    return open_camera(kind, width=160, height=120, quality=80, warmup_sec=1.0, clock=clock or FakeClock())


def test_folder_camera_plays_the_jpegs_in_name_order_in_a_loop(tmp_path):
    for name, data in (('b.jpg', b'B'), ('a.JPG', b'A'), ('c.jpeg', b'C'), ('notes.txt', b'no')):
        (tmp_path / name).write_bytes(data)
    camera = open_kind(f'folder:{tmp_path}')
    assert isinstance(camera, FolderCamera) and camera.name == 'folder'
    assert [camera.capture() for _ in range(4)] == [(b'A', None), (b'B', None), (b'C', None), (b'A', None)]


def test_folder_without_jpegs_says_so(tmp_path):
    with pytest.raises(CameraError, match='no .jpg files'):
        open_kind(f'folder:{tmp_path}')


def test_video_camera_plays_the_clip_in_a_loop(tmp_path):
    clip = tmp_path / 'clip.avi'
    out = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*'MJPG'), 5, (160, 120))
    for shade in (40, 200):
        out.write(np.full((120, 160, 3), shade, np.uint8))
    out.release()
    camera = open_kind(f'video:{clip}')
    assert camera.name == 'video'
    shots = [camera.capture() for _ in range(3)]
    brightness = [cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE).mean() for jpeg, _ in shots]
    assert brightness[0] < 100 < brightness[1] and brightness[2] < 100     # back to the start after the end
    assert all(lux is None for _, lux in shots)
    camera.close()


def test_a_video_that_cannot_be_opened_says_so(tmp_path):
    with pytest.raises(CameraError, match='could not open'):
        open_kind(f'video:{tmp_path / "missing.mp4"}')


def test_unknown_camera_kind_lists_the_choices():
    with pytest.raises(CameraError, match='picamera2'):
        open_kind('webcam-please')


def test_pi_camera_without_picamera2_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, 'picamera2', None)          # import picamera2 fails
    with pytest.raises(CameraError, match='python3-picamera2'):
        open_kind('picamera2')


class FakePicamera2:
    """Stands in for picamera2.Picamera2 (only on a Pi with the camera module)."""

    made: list = []

    def __init__(self):
        self.options = {}
        self.configured = None
        self.started = self.stopped = False
        self.requests = []
        FakePicamera2.made.append(self)

    def create_video_configuration(self, main):
        return {'main': main}

    def configure(self, config):
        self.configured = config

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def capture_request(self):
        request = FakeRequest()
        self.requests.append(request)
        return request


class FakeRequest:
    def __init__(self):
        self.released = False

    def save(self, stream, buffer, format):
        assert (stream, format) == ('main', 'jpeg')
        buffer.write(b'\xff\xd8 from the camera module')

    def get_metadata(self):
        return {'Lux': 123.456, 'ExposureTime': 9000}

    def release(self):
        self.released = True


def test_pi_camera_module_gives_the_picture_and_the_light_level(monkeypatch):
    monkeypatch.setitem(sys.modules, 'picamera2', types.SimpleNamespace(Picamera2=FakePicamera2))
    FakePicamera2.made.clear()
    clock = FakeClock()
    camera = open_kind('picamera2', clock)
    cam = FakePicamera2.made[0]
    assert camera.name == 'picamera2'
    assert cam.configured == {'main': {'size': (160, 120), 'format': 'RGB888'}} and cam.options['quality'] == 80
    assert cam.started and clock.slept == [1.0]                  # exposure gets time to settle
    assert camera.capture() == (b'\xff\xd8 from the camera module', 123.5)
    assert cam.requests[0].released
    camera.close()
    assert cam.stopped


def test_a_folder_that_is_not_there_says_so(tmp_path):
    with pytest.raises(CameraError, match='no folder'):
        open_kind(f'folder:{tmp_path / "missing"}')


class NoCameraAttached(FakePicamera2):
    def __init__(self):
        raise IndexError('list index out of range')          # what Picamera2() does with no camera module


class CannotStart(FakePicamera2):
    def start(self):
        raise RuntimeError('Failed to start camera: Device or resource busy')


@pytest.mark.parametrize('picamera2, why', [(NoCameraAttached, 'list index out of range'),
                                            (CannotStart, 'resource busy')])
def test_a_pi_camera_module_that_cannot_start_says_so_in_plain_words(monkeypatch, picamera2, why):
    monkeypatch.setitem(sys.modules, 'picamera2', types.SimpleNamespace(Picamera2=picamera2))
    with pytest.raises(CameraError, match=f'Pi camera module could not start.*{why}'):
        open_kind('picamera2')
