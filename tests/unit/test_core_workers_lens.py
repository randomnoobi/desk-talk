"""Lens, the camera's own character: says what the camera check finds, and when the Pi is hot, its clock is off
or its own object detection is off."""

from __future__ import annotations

import calendar
from pathlib import Path

from desktalk.config import LensSettings, LensWaits, PiSettings, load_config
from desktalk.core.chat.workers.lens import Lens
from desktalk.domain import CameraNotice, DetectorState, PiHealth, UploadInfo
from desktalk_shared.wire import DETECTOR_TURNED_OFF, info_from_v1_headers
from tests.fakes import (
    FakeCameraHealth,
    FakeClock,
    FakeFrameSource,
    FakePhotoTaker,
    FakePreferences,
    RecordingNotifier,
    SyncEventBus,
)

SETTINGS = LensSettings.model_construct(check_every_sec=2.0, repeat_after_min=20.0, fixed_after_sec=5.0,
                                        wait_before_saying_sec=LensWaits.model_construct(pi=10.0, pi_detector=60.0))
PI = PiSettings.model_construct(hot_warning_c=75.0, clock_off_warning_sec=5.0)
WORDS = load_config(environ={}, dotenv=Path('/nonexistent')).prompts.lens
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
        self.clock_off = None

    def picture(self, received_at, *, temp=None, throttled='0x0', taken_at=None, detector=DetectorState.OK,
                reason='', clock_off=None):
        """`clock_off`: how far off the source judges the Pi's clock (from this picture's times if not given)."""
        taken_at = received_at if taken_at is None else taken_at
        info = UploadInfo(frame_id='pi-000001', taken_at=taken_at, detector=detector, detector_reason=reason,
                          health=PiHealth(cpu_temp_c=temp, throttled=throttled))
        self.latest = (info, received_at)
        self.clock_off = round(taken_at - received_at, 1) if clock_off is None else clock_off

    def latest_upload(self):
        return self.latest

    def clock_off_sec(self):
        return self.clock_off


class Rig:
    def __init__(self, messages=(), kind='pi', phone=None, layout=None, prefs=None, pi=None, words=WORDS,
                 hours_from_utc=0.0):
        # 21 October 2026, 14:00 on the clock (its own time zone, so the test is the same on every computer)
        self.clock = FakeClock(calendar.timegm((2026, 10, 21, 14, 0, 0, 0, 0, 0)) - hours_from_utc * 3600,
                               utc_offset_hours=hours_from_utc)
        self.health = FakeCameraHealth(messages)
        self.source = FakeFrameSource(kind=kind, clock=self.clock)
        self.source.connected, self.source.fps = True, 3.0
        self.notifier = RecordingNotifier(self.clock)
        self.bus = SyncEventBus()
        self.photos = FakePhotoTaker()
        self.pi = pi
        self.lens = Lens(self.health, self.source, layout or Layout(), prefs or FakePreferences(), self.notifier,
                         self.bus, self.photos, SETTINGS, PI, clock=self.clock, prompts=words, preview_chars=120,
                         phone=phone, pi_uploads=pi)

    def tick(self, after=0.0):
        self.clock.advance(after)
        return self.lens.tick(self.clock.now())

    def said(self):
        return [(m['lens'], m['text']) for m in self.notifier.sent]


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
                                  'reference_gravity': (0.0, -9.8, 0.0), 'has_outline': False, 'stopped': False,
                                  'quiet': False}]


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


def test_quiet_hours_go_by_the_clocks_time_zone():
    prefs = FakePreferences({'quiet_enabled': True, 'quiet_start_hour': 13, 'quiet_end_hour': 15})
    rig = Rig([LOST], prefs=prefs, hours_from_utc=5)      # 14:00 on the clock is 09:00 UTC
    rig.tick()
    assert rig.notifier.sent == []


def test_quiet_hours_tell_the_camera_checks_to_stay_quiet_too():
    """So the checks don't count a problem as said, and no "fixed, thanks" follows a problem nobody heard."""
    quiet = Rig(prefs=FakePreferences({'quiet_enabled': True, 'quiet_start_hour': 13, 'quiet_end_hour': 15}))
    quiet.tick()
    awake = Rig(prefs=FakePreferences({'quiet_enabled': False, 'quiet_start_hour': 13, 'quiet_end_hour': 15}))
    awake.tick()
    assert quiet.health.checks[-1]['quiet'] is True
    assert awake.health.checks[-1]['quiet'] is False


def test_quiet_hours_switched_off_let_lens_speak():
    prefs = FakePreferences({'quiet_enabled': False, 'quiet_start_hour': 13, 'quiet_end_hour': 15})
    rig = Rig([LOST], prefs=prefs)
    rig.tick()
    assert len(rig.notifier.sent) == 1


def test_a_photo_goes_with_problems_you_can_see_in_the_picture():
    rig = Rig([{'key': 'glare', 'kind': 'problem', 'text': 'Too much glare.'}])
    rig.tick()
    assert rig.notifier.sent[0]['snapshot'] == 'photo-1'
    assert rig.photos.saved == [(WORDS.photo_label, ())] and WORDS.photo_label == 'Lens: camera view'


def test_no_photo_when_there_is_no_picture_yet():
    rig = Rig([{'key': 'glare', 'kind': 'problem', 'text': 'Too much glare.'}])
    rig.photos.has_picture = False
    rig.tick()
    assert 'snapshot' not in rig.notifier.sent[0]


def test_no_photo_for_other_problems():
    rig = Rig([{'key': 'dark', 'kind': 'problem', 'text': 'It is dark.'}])
    rig.tick()
    assert 'snapshot' not in rig.notifier.sent[0] and rig.photos.saved == []


# ── the Pi's own health ──
def test_what_lens_says_about_the_pi_comes_from_the_prompt_settings():
    words = WORDS.model_copy(update={'pi_hot': 'Hot Pi: {temp:.1f} degrees.',
                                     'pi_fixed': WORDS.pi_fixed.model_copy(update={'pi_hot': 'Cool again.'})})
    rig = Rig(pi=PiReports(), words=words)
    rig.pi.picture(rig.clock.now(), temp=78.44)
    rig.tick()
    rig.tick(after=10)
    rig.pi.picture(rig.clock.now(), temp=50.0)
    rig.tick(after=1)
    rig.tick(after=5)
    assert rig.said() == [('problem', 'Hot Pi: 78.4 degrees.'), ('fixed', 'Cool again.')]


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


def test_a_hot_spell_during_quiet_hours_gets_no_thanks_afterwards():
    """The Pi is not checked during quiet hours, so you never get thanks for a warning you didn't see."""
    prefs = FakePreferences({'quiet_enabled': True, 'quiet_start_hour': 13, 'quiet_end_hour': 15})
    rig = Rig(pi=PiReports(), prefs=prefs)
    rig.pi.picture(rig.clock.now(), temp=80.0)
    rig.tick()
    rig.tick(after=12)
    prefs.update({'quiet_enabled': False})
    rig.pi.picture(rig.clock.now(), temp=60.0)
    rig.tick(after=2)
    rig.tick(after=10)
    assert rig.said() == [] and rig.bus.published == []


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


def test_lens_goes_by_the_pis_clock_as_the_pi_source_judges_it():
    """One picture can arrive late (detection, slow Wi-Fi): the source judges the clock by the quickest recent one."""
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), taken_at=rig.clock.now() - 12.3, clock_off=-0.4)
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


def test_lens_is_quiet_when_detection_on_the_pi_was_turned_off_on_purpose():
    """The camera program was started with --no-detector: nothing to fix, so nothing to say."""
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=DETECTOR_TURNED_OFF)
    for _ in range(3):
        rig.tick(after=SETTINGS.repeat_after_min * 60)
    assert rig.said() == []


def test_lens_is_quiet_when_the_pi_runs_the_old_camera_program():
    """The old camera program (upload format 1) has no detector at all."""
    rig = Rig(pi=PiReports())
    rig.pi.latest = (info_from_v1_headers({'X-Frame-Id': 'pi-000001'}), rig.clock.now())
    for _ in range(3):
        rig.tick(after=SETTINGS.repeat_after_min * 60)
    assert rig.said() == []


def test_lens_still_warns_when_the_pis_detector_is_off_because_it_failed():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason="the detector isn't answering: timed out")
    rig.tick()
    rig.tick(after=60)
    [(kind, text)] = rig.said()
    assert kind == 'problem' and "isn't answering" in text


def test_no_thanks_when_a_failed_detector_is_then_turned_off_on_purpose():
    """Lens warned, then the camera program was restarted with --no-detector: the Pi still isn't detecting,
    so "finding the objects itself again" would be wrong. Lens just drops it."""
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=NO_CONTAINER)
    rig.tick()
    rig.tick(after=60)
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=DETECTOR_TURNED_OFF)
    rig.tick(after=2)
    rig.tick(after=5)
    rig.tick(after=SETTINGS.repeat_after_min * 60)
    assert [kind for kind, _ in rig.said()] == ['problem']
