"""DetectionProducer: camera pictures → detection events, the live view and the desk state.
Driven by calling tick() with a FakeClock, a FakeFrameSource and a FakeDetector: no threads, no waiting."""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from desktalk.adapters.camera import PiFrameSource
from desktalk.config import CameraSettings
from desktalk.core.vision.producer import DetectionProducer
from desktalk.desk_state import DeskState
from desktalk.domain import BBox, Detection, DetectionEvent, DetectorState, Light, UploadInfo
from tests.fakes import FakeClock, FakeDetector, FakeFrameSource, FakePainter, FakePictureCheck, SyncEventBus

ROOT = Path(__file__).resolve().parents[2]

LAPTOP = Detection('laptop', 0.9, BBox(200, 200, 400, 350))
CUP = Detection('cup', 0.8, BBox(560, 380, 600, 450))
PERSON = Detection('person', 0.95, BBox(400, 0, 640, 300))


def camera_settings(**changes) -> CameraSettings:
    """The camera section of config/settings.yaml, with some values changed for a test."""
    data = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())['camera']
    return CameraSettings.model_validate({**data, **changes})


def desk_picture(value=140, width=640, height=480):
    picture = np.full((height, width, 3), value, np.uint8)
    picture[::8] = 40                      # some texture, like a real desk
    return picture


def jpeg_of(picture, quality=90) -> bytes:
    return cv2.imencode('.jpg', picture, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def from_pi(state, detections=(), frame_id='pi-000007', **more) -> UploadInfo:
    return UploadInfo(frame_id=frame_id, taken_at=more.pop('taken_at', None), detector=state,
                      detections=tuple(detections), **more)


class RecordingSource(FakeFrameSource):
    """A FakeFrameSource that remembers how long each read() was asked to wait."""

    def __init__(self, kind, clock):
        super().__init__(kind, clock)
        self.waits: list[float] = []

    def read(self, timeout):
        self.waits.append(timeout)
        return super().read(timeout)


class Rig:
    """A producer with fakes all around it."""

    def __init__(self, kind='pi', detector=None, painter=None, new_session_after_sec=300.0, **camera):
        self.clock = FakeClock()
        self.source = RecordingSource(kind, self.clock)
        self.detector = detector if detector is not None else FakeDetector([LAPTOP, CUP])
        self.bus = SyncEventBus()
        self.desk = DeskState()
        self.lens = FakePictureCheck()
        self.painter = painter if painter is not None else FakePainter()
        self.producer = DetectionProducer(self.source, self.detector, self.bus, self.desk, self.clock, self.lens,
                                          self.painter, camera_settings(**camera),
                                          new_session_after_sec=new_session_after_sec)

    def picture(self, picture=None, info=None, jpeg=None):
        """A picture arrives from the camera; returns the wait tick() asks for."""
        picture = desk_picture() if picture is None else picture
        if jpeg is None and info is not None:
            jpeg = jpeg_of(picture)
        self.source.add(picture, jpeg=jpeg, info=info)
        return self.producer.tick(self.clock.now())

    def events(self) -> list[DetectionEvent]:
        return self.bus.of_type(DetectionEvent)


# ── timing ───────────────────────────────────────────────────────────
def test_with_no_new_picture_it_goes_again_right_away():
    rig = Rig()
    assert rig.producer.tick(rig.clock.now()) == 0
    assert rig.events() == []


def test_waits_for_a_picture_as_long_as_the_settings_say():
    rig = Rig(wait_for_picture_sec=0.7)
    rig.producer.tick(rig.clock.now())
    assert rig.source.waits == [0.7]


def test_after_a_picture_it_waits_until_the_next_one_is_due():
    assert Rig(fps=4).picture() == 0.25


@pytest.mark.parametrize('detect_sec, wait', [(0.1, 0.15), (0.4, 0.0)])
def test_time_spent_detecting_comes_off_the_wait(detect_sec, wait):
    rig = Rig(fps=4, kind='webcam')
    rig.detector.clock, rig.detector.delay = rig.clock, detect_sec
    assert rig.picture() == pytest.approx(wait)


def test_is_a_background_worker():
    from desktalk.ports import PeriodicWorker
    assert isinstance(Rig().producer, PeriodicWorker) and Rig().producer.name


# ── what the Pi's detector said ──────────────────────────────────────
def test_ok_uses_the_pis_detections_and_detects_nothing_here():
    rig = Rig()
    rig.picture(info=from_pi(DetectorState.OK, [CUP], detect_ms=240.0))
    [event] = rig.events()
    assert event.detections == (CUP,) and event.detected_on == 'pi'
    assert rig.detector.calls == 0


def test_ok_boxes_reaching_outside_the_picture_are_cut_to_it():
    rig = Rig()
    wide = Detection('keyboard', 0.7, BBox(500, 400, 900, 700))
    gone = Detection('mug', 0.7, BBox(700, 10, 800, 60))
    rig.picture(desk_picture(width=640, height=480), info=from_pi(DetectorState.OK, [wide, gone]))
    assert rig.events()[0].detections == (Detection('keyboard', 0.7, BBox(500, 400, 640, 480)),)


def test_busy_is_for_the_live_view_only():
    rig = Rig()
    rig.picture(info=from_pi(DetectorState.BUSY))
    assert rig.events() == [] and rig.detector.calls == 0
    assert rig.lens.samples == []
    with rig.desk.lock:
        assert rig.desk.raw_frames.get(0) and rig.desk.frames.get(0)
        assert rig.desk.cameras == {0: True}                 # the app shows this camera's live view


def test_off_is_detected_on_this_computer():
    rig = Rig()
    rig.picture(info=from_pi(DetectorState.OFF, detector_reason='the detector container is not answering'))
    [event] = rig.events()
    assert event.detections == (LAPTOP, CUP) and event.detected_on == 'computer'
    assert rig.detector.calls == 1


@pytest.mark.parametrize('kind', ['phone', 'webcam'])
def test_cameras_without_a_detector_are_detected_on_this_computer(kind):
    rig = Rig(kind=kind)
    rig.picture()
    [event] = rig.events()
    assert event.detections == (LAPTOP, CUP) and event.detected_on == 'computer' and event.source == kind


# ── what the event says ──────────────────────────────────────────────
def test_the_event_says_what_was_seen_where_and_when():
    rig = Rig()
    rig.clock.advance(100)
    rig.picture(desk_picture(width=320, height=240),
                info=from_pi(DetectorState.OK, [Detection('cup', 0.8, BBox(10, 10, 50, 60))], lux=312.5,
                             taken_at=1_760_000_099.5))
    [event] = rig.events()
    assert event.ts == 1_760_000_100.0 and event.taken_at == 1_760_000_099.5
    assert event.frame_id == 'pi-000007' and event.source == 'pi' and event.size == (320, 240)
    assert event.person is False and event.moved is False and event.light.lux == 312.5


def test_pictures_without_an_id_are_numbered_by_camera():
    rig = Rig(kind='phone')
    rig.picture()
    rig.picture()
    assert [e.frame_id for e in rig.events()] == ['phone-000001', 'phone-000002']


def test_person_is_true_when_someone_is_in_the_picture():
    rig = Rig(detector=FakeDetector([LAPTOP, PERSON]))
    rig.picture()
    assert rig.events()[0].person is True


@pytest.mark.parametrize('picture, brightness', [
    (np.full((480, 640, 3), 100, np.uint8), 100.0),
    (np.hstack([np.zeros((480, 320, 3), np.uint8), np.full((480, 320, 3), 255, np.uint8)]), 127.5),
    (np.dstack([np.zeros((480, 640), np.uint8)] * 2 + [np.full((480, 640), 255, np.uint8)]), 76.0),  # red
])
def test_light_is_the_average_brightness_of_the_picture(picture, brightness):
    rig = Rig(kind='webcam')
    rig.picture(picture)
    assert rig.events()[0].light == Light(brightness=brightness, lux=None)


def test_lens_sees_every_analysed_picture():
    rig = Rig()
    picture = desk_picture()
    rig.clock.advance(5)
    rig.picture(picture, info=from_pi(DetectorState.OFF))
    assert len(rig.lens.samples) == 1
    seen, detections, now = rig.lens.samples[0]
    assert seen is picture and detections == [LAPTOP, CUP] and now == 1_760_000_005.0


def test_moved_is_what_lens_says_about_the_camera():
    rig = Rig()
    rig.lens.moved = True
    rig.picture()
    assert rig.events()[0].moved is True


def test_the_event_time_is_when_the_picture_was_picked_up_not_after_detecting():
    rig = Rig(kind='webcam')
    rig.detector.clock, rig.detector.delay = rig.clock, 0.4
    rig.picture()
    assert rig.events()[0].ts == 1_760_000_000.0


# ── the desk right now (DeskState) ───────────────────────────────────
def test_the_session_starts_with_the_first_analysed_picture():
    rig = Rig()
    rig.picture(info=from_pi(DetectorState.BUSY))
    assert rig.desk.session_start is None                     # live view only
    rig.clock.advance(1)
    rig.picture(info=from_pi(DetectorState.OK))
    assert rig.desk.session_start == 1_760_000_001.0


@pytest.mark.parametrize('gap, new_session', [(299.0, False), (300.0, True)])
def test_a_long_gap_between_pictures_starts_a_new_session(gap, new_session):
    rig = Rig(new_session_after_sec=300)
    rig.picture()
    rig.clock.advance(gap)
    rig.picture()
    assert rig.desk.session_start == (1_760_000_000.0 + gap if new_session else 1_760_000_000.0)


def test_the_desk_knows_the_picture_size_the_light_and_that_the_camera_works():
    rig = Rig()
    rig.picture(np.full((240, 320, 3), 90, np.uint8), info=from_pi(DetectorState.OK, lux=312.5))
    with rig.desk.lock:
        assert (rig.desk.frame_w, rig.desk.frame_h) == (320, 240)
        assert rig.desk.light == {'brightness': 90.0, 'lux': 312.5}
        assert rig.desk.cameras == {0: True}


def test_the_desk_remembers_when_a_person_or_movement_was_last_seen():
    rig = Rig(detector=FakeDetector([LAPTOP]))
    rig.picture()
    assert rig.desk.person_last_seen is None and rig.desk.last_motion_ts is None
    rig.clock.advance(10)
    rig.detector.detections = [LAPTOP, PERSON]
    rig.picture()
    rig.clock.advance(10)
    rig.detector.detections = [LAPTOP]
    rig.lens.moved = True
    rig.picture()
    assert rig.desk.person_last_seen == 1_760_000_010.0
    assert rig.desk.last_motion_ts == 1_760_000_020.0


def test_seen_labels_has_the_best_box_of_each_label_as_the_app_reads_it():
    weak_cup = Detection('cup', 0.6, BBox(0, 0, 10, 10))
    rig = Rig(detector=FakeDetector([weak_cup, CUP, LAPTOP]))
    rig.picture()
    with rig.desk.lock:
        assert rig.desk.seen_labels == {
            'cup': {'label': 'cup', 'conf': 0.8, 'bbox': [560.0, 380.0, 600.0, 450.0], 'center': [580.0, 415.0]},
            'laptop': {'label': 'laptop', 'conf': 0.9, 'bbox': [200.0, 200.0, 400.0, 350.0], 'center': [300.0, 275.0]},
        }


# ── the live view ────────────────────────────────────────────────────
class MarkingPainter:
    """Paints the whole picture white and remembers the detections it was given."""

    def __init__(self):
        self.given: list[list[Detection]] = []

    def paint(self, picture, detections):
        self.given.append(list(detections))
        return np.full_like(picture, 255)


def decoded(jpeg):
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def test_the_plain_live_view_is_the_pis_own_jpeg():
    rig = Rig()
    sent = jpeg_of(desk_picture(), quality=55)
    rig.picture(desk_picture(), info=from_pi(DetectorState.OK), jpeg=sent)
    with rig.desk.lock:
        assert rig.desk.raw_frames[0] == sent


def test_pictures_without_a_jpeg_are_saved_at_the_jpeg_quality_setting():
    rig = Rig(kind='webcam', jpeg_quality=40)
    picture = desk_picture()
    rig.picture(picture)
    with rig.desk.lock:
        assert rig.desk.raw_frames[0] == jpeg_of(picture, quality=40)


def test_the_boxes_live_view_is_what_the_painter_drew():
    painter = MarkingPainter()
    rig = Rig(kind='webcam', painter=painter, boxes_jpeg_quality=60)
    rig.picture()
    assert painter.given == [[LAPTOP, CUP]]
    with rig.desk.lock:
        assert rig.desk.frames[0] == jpeg_of(np.full((480, 640, 3), 255, np.uint8), quality=60)


def test_another_camera_number_keeps_its_own_live_view():
    clock, source, desk = FakeClock(), FakeFrameSource('webcam'), DeskState()
    producer = DetectionProducer(source, FakeDetector([CUP]), SyncEventBus(), desk, clock, FakePictureCheck(),
                                 FakePainter(), camera_settings(), new_session_after_sec=300, camera_index=2)
    source.add(desk_picture())
    producer.tick(clock.now())
    assert set(desk.raw_frames) == set(desk.frames) == set(desk.cameras) == {2}


def test_busy_pictures_are_painted_with_the_newest_boxes_so_they_dont_flicker():
    painter = MarkingPainter()
    rig = Rig(painter=painter)
    rig.picture(info=from_pi(DetectorState.OK, [CUP]))
    rig.picture(info=from_pi(DetectorState.BUSY))
    assert painter.given == [[CUP], [CUP]]


# ── when something fails ─────────────────────────────────────────────
def test_when_detection_here_fails_the_live_view_goes_on_without_boxes_or_events(caplog):
    painter = MarkingPainter()
    rig = Rig(detector=FakeDetector(fail='the model file is missing'), painter=painter)
    rig.picture(info=from_pi(DetectorState.OFF))
    assert rig.events() == [] and rig.lens.samples == []
    assert painter.given == [[]]
    with rig.desk.lock:
        assert rig.desk.raw_frames.get(0) and rig.desk.frames.get(0)
    assert 'the model file is missing' in caplog.text
    assert rig.producer.problem == 'the model file is missing'


def test_a_detection_problem_is_logged_once_and_again_when_it_is_fixed(caplog):
    caplog.set_level(logging.INFO)
    rig = Rig(kind='webcam', detector=FakeDetector(fail='the model file is missing'))
    rig.picture()
    rig.picture()
    assert caplog.text.count('the model file is missing') == 1
    rig.detector.fail = ''
    caplog.clear()
    rig.picture()
    assert len(rig.events()) == 1 and rig.producer.problem is None
    assert 'works again' in caplog.text


def test_when_the_painter_fails_the_event_still_goes_out_and_the_boxes_view_shows_the_plain_picture(caplog):
    class BrokenPainter:
        def paint(self, picture, detections):
            raise ValueError('bad desk outline')

    rig = Rig(kind='webcam', painter=BrokenPainter(), boxes_jpeg_quality=60)
    picture = desk_picture()
    rig.picture(picture)
    assert len(rig.events()) == 1
    with rig.desk.lock:
        assert rig.desk.frames[0] == jpeg_of(picture, quality=60)
    assert 'bad desk outline' in caplog.text


# ── what the app shows about detection ───────────────────────────────
def test_counts_the_analysed_pictures():
    rig = Rig()
    assert rig.producer.pictures_analysed == 0
    rig.picture(info=from_pi(DetectorState.OK))
    rig.picture(info=from_pi(DetectorState.BUSY))
    rig.picture(info=from_pi(DetectorState.OFF))
    assert rig.producer.pictures_analysed == 2


def test_detection_time_is_measured_here_or_taken_from_the_pi():
    rig = Rig(detector=FakeDetector([CUP], delay=0.25))
    rig.detector.clock = rig.clock
    assert rig.producer.last_detect_ms is None
    rig.picture(info=from_pi(DetectorState.OFF))
    assert rig.producer.last_detect_ms == pytest.approx(250.0)
    rig.picture(info=from_pi(DetectorState.OK, detect_ms=180.0))
    assert rig.producer.last_detect_ms == 180.0
    rig.picture(info=from_pi(DetectorState.OK))                 # the Pi didn't say: the last time stays
    assert rig.producer.last_detect_ms == 180.0


# ── with the real Pi camera source ───────────────────────────────────
def test_a_pi_upload_goes_from_the_pi_source_through_the_producer():
    clock, bus, desk = FakeClock(), SyncEventBus(), DeskState()
    source = PiFrameSource(clock, camera_settings())
    detector = FakeDetector([LAPTOP])
    producer = DetectionProducer(source, detector, bus, desk, clock, FakePictureCheck(), FakePainter(),
                                 camera_settings(), new_session_after_sec=300)
    sent = jpeg_of(desk_picture())
    assert source.push(sent, from_pi(DetectorState.OK, [CUP], frame_id='pi-000042', lux=200.0))
    producer.tick(clock.now())
    assert source.push(sent, from_pi(DetectorState.OFF, frame_id='pi-000043'))
    producer.tick(clock.now())
    first, second = bus.of_type(DetectionEvent)
    assert (first.frame_id, first.detections, first.detected_on, first.light.lux) == ('pi-000042', (CUP,), 'pi', 200.0)
    assert (second.frame_id, second.detections, second.detected_on) == ('pi-000043', (LAPTOP,), 'computer')
    with desk.lock:
        assert desk.raw_frames[0] == sent
