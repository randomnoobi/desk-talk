"""Lens, the camera's own character: says what the camera check finds, and when the Pi is hot, its clock is off
or its own object detection is off."""

from __future__ import annotations

import time

import cv2
import numpy as np

from desktalk.config import LensSettings, LensWaits, PiSettings, SnapshotsSettings
from desktalk.core.chat.workers.lens import Lens
from desktalk.core.vision.proof_photos import ProofPhotos
from desktalk.desk_state import DeskState
from desktalk.domain import CameraNotice, DetectorState, PiHealth, UploadInfo
from tests.fakes import FakeCameraHealth, FakeClock, FakeFrameSource, FakePreferences, RecordingNotifier, SyncEventBus

SETTINGS = LensSettings.model_construct(check_every_sec=2.0, repeat_after_min=20.0, fixed_after_sec=5.0,
                                        wait_before_saying_sec=LensWaits.model_construct(pi=10.0, pi_detector=60.0))
PI = PiSettings.model_construct(hot_warning_c=75.0, clock_off_warning_sec=5.0)
SNAPSHOTS = SnapshotsSettings(max_kept=150, crop_padding=0.25, crop_min_px=40, jpeg_quality=85)
LOST = {'key': 'lost', 'kind': 'problem', 'text': "I can't see anything: the Raspberry Pi stopped sending pictures."}


class Layout:
    def __init__(self, outline=True, gravity=None):
        self.outline, self.gravity = outline, gravity

    def has_outline(self):
        return self.outline

    def reference_gravity(self):
        return self.gravity


class Phone:
    def __init__(self, gravity=None, stopped=False):
        self.gravity, self.stopped_by_user = gravity, stopped

    def recent_gravity(self):
        return self.gravity


class PiReports:
    """What the Pi sent with its newest picture, as PiFrameSource keeps it."""

    def __init__(self):
        self.latest = None

    def picture(self, received_at, *, temp=None, throttled='0x0', taken_at=None, detector=DetectorState.OK,
                reason=''):
        info = UploadInfo(frame_id='pi-000001', taken_at=received_at if taken_at is None else taken_at,
                          detector=detector, detector_reason=reason,
                          health=PiHealth(cpu_temp_c=temp, throttled=throttled))
        self.latest = (info, received_at)

    def latest_upload(self):
        return self.latest


class Rig:
    def __init__(self, messages=(), kind='pi', phone=None, layout=None, prefs=None, pi=None):
        self.clock = FakeClock(time.mktime((2026, 10, 21, 14, 0, 0, 0, 0, -1)))
        self.health = FakeCameraHealth(messages)
        self.source = FakeFrameSource(kind=kind, clock=self.clock)
        self.source.connected, self.source.fps = True, 3.0
        self.notifier = RecordingNotifier(self.clock)
        self.bus = SyncEventBus()
        self.desk = DeskState()
        self.pi = pi
        self.lens = Lens(self.health, self.source, layout or Layout(), prefs or FakePreferences(), self.notifier,
                         self.bus, ProofPhotos(self.desk, self.clock, SNAPSHOTS), SETTINGS, PI, phone=phone,
                         pi_uploads=pi)

    def tick(self, after=0.0):
        self.clock.advance(after)
        return self.lens.tick(self.clock.now())

    def said(self):
        return [(m['lens'], m['text']) for m in self.notifier.sent]


def a_jpeg():
    return cv2.imencode('.jpg', np.zeros((48, 64, 3), np.uint8))[1].tobytes()


# ── what the camera check finds ──
def test_lens_says_the_camera_problem_in_the_chat_and_logs_it():
    rig = Rig([LOST])
    assert rig.tick() == 2.0
    assert [(m['type'], m['character'], m['text'], m['lens'], m['toast']) for m in rig.notifier.sent] == [
        ('group_message', 'lens', LOST['text'], 'problem', True)]
    notice = rig.bus.of_type(CameraNotice)
    assert [(n.key, n.kind, n.text, n.ts) for n in notice] == [('lost', 'problem', LOST['text'], rig.clock.now())]


def test_a_thank_you_is_not_a_pop_up():
    rig = Rig([{'key': 'dark', 'kind': 'fixed', 'text': 'Much better, thanks for the light!'}])
    rig.tick()
    assert rig.notifier.sent[0]['toast'] is False and rig.notifier.sent[0]['lens'] == 'fixed'


def test_nothing_to_say_sends_nothing():
    rig = Rig()
    rig.tick()
    assert rig.notifier.sent == [] and rig.bus.published == []


def test_the_check_gets_the_pi_cameras_state_and_the_desk_layout():
    rig = Rig(layout=Layout(outline=False, gravity=(0.0, -9.8, 0.0)))
    rig.tick()
    assert rig.health.checks == [{'now': rig.clock.now(), 'connected': True, 'fps': 3.0, 'gravity': None,
                                  'reference_gravity': (0.0, -9.8, 0.0), 'has_outline': False, 'stopped': False}]


def test_the_check_gets_the_phones_tilt_and_stop_button():
    rig = Rig(kind='phone', phone=Phone(gravity=(0.1, -9.7, 0.3), stopped=True))
    rig.source.connected = False
    rig.tick()
    check = rig.health.checks[0]
    assert (check['connected'], check['gravity'], check['stopped']) == (False, (0.1, -9.7, 0.3), True)


def test_a_webcam_is_not_checked_for_a_slow_picture_rate():
    rig = Rig(kind='webcam')
    rig.tick()
    assert rig.health.checks[0]['fps'] is None


def test_quiet_hours_keep_lens_quiet():
    prefs = FakePreferences({'quiet_enabled': True, 'quiet_start_hour': 13, 'quiet_end_hour': 15})
    rig = Rig([LOST], prefs=prefs)
    rig.tick()
    assert rig.notifier.sent == [] and rig.bus.published == []


def test_quiet_hours_switched_off_let_lens_speak():
    prefs = FakePreferences({'quiet_enabled': False, 'quiet_start_hour': 13, 'quiet_end_hour': 15})
    rig = Rig([LOST], prefs=prefs)
    rig.tick()
    assert len(rig.notifier.sent) == 1


def test_a_photo_goes_with_problems_you_can_see_in_the_picture():
    rig = Rig([{'key': 'glare', 'kind': 'problem', 'text': 'Too much glare.'}])
    rig.desk.frames[0] = a_jpeg()
    rig.tick()
    snap = rig.notifier.sent[0]['snapshot']
    assert rig.desk.snapshot_order[0]['id'] == snap and rig.desk.snapshot_order[0]['label'] == 'Lens: camera view'
    assert rig.desk.snapshot_order[0]['ts'] == rig.clock.now()


def test_no_photo_for_other_problems():
    rig = Rig([{'key': 'dark', 'kind': 'problem', 'text': 'It is dark.'}])
    rig.desk.frames[0] = a_jpeg()
    rig.tick()
    assert 'snapshot' not in rig.notifier.sent[0] and rig.desk.snapshot_order == []


# ── the Pi's own health ──
def test_lens_mentions_a_hot_pi_once_it_has_been_hot_a_while():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=78.4)
    rig.tick()
    rig.tick(after=8)
    assert rig.said() == []
    rig.tick(after=2)
    [(kind, text)] = rig.said()
    assert kind == 'problem' and '78' in text and 'Raspberry Pi' in text
    assert [(n.key, n.kind) for n in rig.bus.of_type(CameraNotice)] == [('pi_hot', 'problem')]


def test_lens_says_a_hot_pi_again_only_after_the_repeat_time():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=80.0)
    rig.tick()
    rig.tick(after=10)
    rig.tick(after=19 * 60)
    assert len(rig.said()) == 1
    rig.tick(after=60)
    assert len(rig.said()) == 2


def test_lens_says_thanks_when_the_pi_has_cooled_down_for_a_while():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=80.0)
    rig.tick()
    rig.tick(after=10)
    rig.pi.picture(rig.clock.now(), temp=60.0)
    rig.tick(after=2)
    rig.tick(after=4)
    assert [kind for kind, _ in rig.said()] == ['problem']
    rig.tick(after=1)
    assert [kind for kind, _ in rig.said()] == ['problem', 'fixed']
    assert rig.bus.of_type(CameraNotice)[-1].key == 'pi_hot'


def test_a_short_hot_spell_is_not_mentioned():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=76.0)
    rig.tick()
    rig.pi.picture(rig.clock.now(), temp=70.0)
    rig.tick(after=6)
    rig.pi.picture(rig.clock.now(), temp=76.0)
    rig.tick(after=6)
    rig.tick(after=6)
    assert rig.said() == []


def test_lens_mentions_a_pi_that_is_slowing_itself_down():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=70.0, throttled='0xe0004')
    rig.tick()
    rig.tick(after=10)
    [(kind, text)] = rig.said()
    assert kind == 'problem' and 'slowing' in text


def test_lens_mentions_a_weak_power_supply():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=50.0, throttled='0x50005')
    rig.tick()
    rig.tick(after=10)
    [(kind, text)] = rig.said()
    assert 'power' in text
    assert rig.bus.of_type(CameraNotice)[0].key == 'pi_power'


def test_slow_downs_earlier_since_the_pi_started_are_not_mentioned():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=50.0, throttled='0x50000')
    rig.tick()
    rig.tick(after=10)
    assert rig.said() == []


def test_lens_mentions_a_pi_clock_that_is_behind():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), taken_at=rig.clock.now() - 12.3)
    rig.tick()
    rig.tick(after=10)
    [(kind, text)] = rig.said()
    assert kind == 'problem' and '12 seconds behind' in text
    assert rig.bus.of_type(CameraNotice)[0].key == 'pi_clock'


def test_lens_mentions_a_pi_clock_that_is_ahead():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), taken_at=rig.clock.now() + 40)
    rig.tick()
    rig.tick(after=10)
    assert '40 seconds ahead' in rig.said()[0][1]


def test_a_picture_that_took_a_moment_to_arrive_is_not_a_clock_problem():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), taken_at=rig.clock.now() - 4.5)
    rig.tick()
    rig.tick(after=10)
    assert rig.said() == []


def test_the_pi_is_not_checked_while_its_pictures_have_stopped():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=90.0)
    rig.source.connected = False
    rig.tick()
    rig.tick(after=10)
    assert rig.said() == []


def test_the_pi_is_not_checked_before_its_first_picture():
    rig = Rig(pi=PiReports())
    rig.tick()
    rig.tick(after=10)
    assert rig.said() == []


# ── the Pi's own object detection ──
NO_CONTAINER = 'the detector container is not answering'


def test_lens_says_why_the_pis_own_detection_is_off_once_it_has_been_off_a_while():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=NO_CONTAINER)
    rig.tick()
    rig.tick(after=59)
    assert rig.said() == []
    rig.tick(after=1)
    [(kind, text)] = rig.said()
    assert kind == 'problem' and NO_CONTAINER in text and 'This computer' in text
    assert [(n.key, n.kind) for n in rig.bus.of_type(CameraNotice)] == [('pi_detector', 'problem')]


def test_busy_is_not_off():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.BUSY)
    rig.tick()
    rig.tick(after=60)
    assert rig.said() == []


def test_a_short_spell_without_detection_on_the_pi_is_not_mentioned():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=NO_CONTAINER)
    rig.tick()
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OK)
    rig.tick(after=40)
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=NO_CONTAINER)
    rig.tick(after=40)
    assert rig.said() == []


def test_lens_says_thanks_when_the_pi_detects_again():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=NO_CONTAINER)
    rig.tick()
    rig.tick(after=60)
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OK)
    rig.tick(after=2)
    rig.tick(after=5)
    assert [kind for kind, _ in rig.said()] == ['problem', 'fixed']
    assert rig.bus.of_type(CameraNotice)[-1].key == 'pi_detector'
