"""Lens, the camera's own character: says what the camera check finds, and when the Pi is hot, its clock is off
or its own object detection is off, and what the health checks find (no new detections, a full card, the Pi's
heartbeat stopping)."""

from __future__ import annotations

import calendar
import logging
from pathlib import Path

from desktalk.config import HealthChecksSettings, LensSettings, LensWaits, PiSettings, load_config
from desktalk.core.chat.workers.lens import Lens
from desktalk.core.health import HealthChecks
from desktalk.domain import CameraNotice, DetectorState, PiHealth, Saving, UploadInfo
from desktalk.ports import CameraWarnings
from desktalk_shared.wire import DETECTOR_TURNED_OFF, info_from_v1_headers
from tests.fakes import (
    FakeCameraHealth,
    FakeClock,
    FakeDetectionTimes,
    FakeDiskSpace,
    FakeFrameSource,
    FakePhotoTaker,
    FakePreferences,
    RecordingNotifier,
    SyncEventBus,
)

SETTINGS = LensSettings.model_construct(check_every_sec=2.0, repeat_after_min=20.0, fixed_after_sec=5.0,
                                        wait_before_saying_sec=LensWaits.model_construct(pi=10.0, pi_detector=60.0))
PI = PiSettings.model_construct(hot_warning_c=75.0, hot_serious_c=80.0, clock_off_warning_sec=5.0)
CHECKS = HealthChecksSettings.model_construct(card_warning_below_mb=2000.0, card_serious_below_mb=500.0,
                                              no_detections_warning_sec=300.0, no_detections_serious_sec=900.0,
                                              pi_heartbeat_late_sec=90.0, room_warning_below_mb=300.0,
                                              room_serious_below_mb=100.0, card_rest_warning_below_mb=1000.0,
                                              card_rest_serious_below_mb=300.0)
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
    """What the Pi sent with its newest picture and heartbeat, as PiFrameSource keeps it (the Rig hands it its
    clock, for when they came in)."""

    def __init__(self):
        self.latest = None
        self.clock_off = None
        self.clock = FakeClock()
        self._health = None             # (health, clock.monotonic())
        self._beat_at = None

    def heartbeat(self, temp=None):
        self._beat_at = self.clock.monotonic()
        self._health = (PiHealth(cpu_temp_c=temp), self._beat_at)

    def last_heartbeat_at(self):
        return self._beat_at

    def latest_health(self):
        return self._health

    def picture(self, received_at, *, temp=None, throttled='0x0', taken_at=None, detector=DetectorState.OK,
                reason='', clock_off=None):
        """`clock_off`: how far off the source judges the Pi's clock (from this picture's times if not given)."""
        taken_at = received_at if taken_at is None else taken_at
        info = UploadInfo(frame_id='pi-000001', taken_at=taken_at, detector=detector, detector_reason=reason,
                          health=PiHealth(cpu_temp_c=temp, throttled=throttled))
        self.latest = (info, received_at)
        self._health = (info.health, self.clock.monotonic())
        self.clock_off = round(taken_at - received_at, 1) if clock_off is None else clock_off

    def latest_upload(self):
        return self.latest

    def clock_off_sec(self):
        return self.clock_off


class Rig:
    """`detecting`: pictures go through detection at every tick (set it False to stop them). `free_mb`: the card."""

    def __init__(self, messages=(), kind='pi', phone=None, layout=None, prefs=None, pi=None, words=WORDS,
                 hours_from_utc=0.0, free_mb=10_000.0, saving=Saving.PLAIN, rest_mb=10_000.0):
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
        if pi is not None:
            pi.clock = self.clock
        self.detecting = True
        self.detections = FakeDetectionTimes()
        self.disk = FakeDiskSpace(free_mb)
        self.rest = FakeDiskSpace(rest_mb)
        checks = HealthChecks(self.source, self.detections, self.disk, CHECKS, PI, clock=self.clock, pi=pi,
                              saving=saving, card=self.rest)
        self.lens = Lens(self.health, self.source, layout or Layout(), prefs or FakePreferences(), self.notifier,
                         self.bus, self.photos, SETTINGS, PI, clock=self.clock, prompts=words, preview_chars=120,
                         checks=checks, phone=phone, pi_uploads=pi, saving=saving)

    def tick(self, after=0.0):
        self.clock.advance(after)
        if self.detecting:
            self.detections.found_at = self.clock.monotonic()
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


def test_lens_problem_flagged_fixed_not():
    rig = Rig([LOST, {'key': 'dark', 'kind': 'fixed', 'text': 'Much better, thanks for the light!'}])
    rig.tick()
    problem, fixed = rig.notifier.sent
    assert problem['notify'] == 'warning' and 'notify' not in fixed


def test_a_pi_problem_is_flagged_as_a_warning():
    pi = PiReports()
    rig = Rig(pi=pi)
    pi.picture(rig.clock.now(), temp=80.0)
    rig.tick()
    rig.tick(after=15)
    assert [m['notify'] for m in rig.notifier.sent] == ['warning']


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


def test_an_old_temperature_is_not_checked_while_the_pictures_have_stopped():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=90.0)
    rig.source.connected = False
    rig.tick(after=CHECKS.pi_heartbeat_late_sec)
    rig.tick(after=10)
    assert rig.said() == []


def test_the_heartbeat_keeps_the_temperature_checked_while_the_pictures_have_stopped():
    rig = Rig(pi=PiReports())
    rig.source.connected = False
    rig.pi.heartbeat(temp=78.0)
    rig.tick()
    rig.tick(after=10)
    [(kind, text)] = rig.said()
    assert kind == 'problem' and '78' in text
    assert rig.bus.of_type(CameraNotice)[0].key == 'pi_hot'


def test_a_pi_that_gets_far_too_hot_is_said_at_once_not_after_the_repeat_time():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=76.0)
    rig.tick()
    rig.tick(after=10)
    rig.pi.picture(rig.clock.now(), temp=82.0)
    rig.tick(after=2)
    assert [text for _, text in rig.said()] == [WORDS.pi_hot.format(temp=76.0), WORDS.pi_very_hot.format(temp=82.0)]
    rig.pi.picture(rig.clock.now(), temp=77.0)
    rig.tick(after=2)
    rig.pi.picture(rig.clock.now(), temp=82.0)
    rig.tick(after=2)
    assert len(rig.said()) == 2             # once said, serious again waits for the repeat time


def test_a_pi_hovering_around_the_warning_temperature_is_said_once_until_the_repeat_time():
    """Hot for 20 seconds, cool for one tick, hot again: one short dip isn't a fix, so it's not said again."""
    rig = Rig(pi=PiReports())
    for _ in range(5):
        for _ in range(10):
            rig.pi.picture(rig.clock.now(), temp=76.0)
            rig.tick(after=2)
        rig.pi.picture(rig.clock.now(), temp=74.0)
        rig.tick(after=2)
    assert rig.said() == [('problem', WORDS.pi_hot.format(temp=76.0))]


def test_a_pi_problem_said_again_after_its_thanks_is_said_at_once():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), temp=76.0)
    rig.tick()
    rig.tick(after=10)
    rig.pi.picture(rig.clock.now(), temp=60.0)
    rig.tick(after=2)
    rig.tick(after=5)
    rig.pi.picture(rig.clock.now(), temp=77.0)
    rig.tick(after=2)
    rig.tick(after=10)
    assert [kind for kind, _ in rig.said()] == ['problem', 'fixed', 'problem']


def test_the_pi_is_not_checked_before_its_first_picture():
    rig = Rig(pi=PiReports())
    rig.tick()
    rig.tick(after=10)
    assert rig.said() == []


# ── the Pi's own object detection ──
NO_CONTAINER = 'the detector container is not answering'


def test_lens_says_the_pis_own_detection_is_off_once_it_has_been_off_a_while():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=NO_CONTAINER)
    rig.tick()
    rig.tick(after=59)
    assert rig.said() == []
    rig.tick(after=1)
    [(kind, text)] = rig.said()
    assert kind == 'problem' and 'This computer' not in text and 'docker compose' in text
    assert [(n.key, n.kind) for n in rig.bus.of_type(CameraNotice)] == [('pi_detector', 'problem')]


def test_the_raw_reason_goes_in_the_log_and_is_never_said(caplog):
    """It gets read out on the Pi's speaker, and "[Errno -2] Name or service not known" means nothing to anyone."""
    raw = "the detector isn't answering: [Errno -2] Name or service not known"
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=raw)
    with caplog.at_level(logging.WARNING, logger='desktalk.core.chat.workers.lens'):
        rig.tick()
        rig.tick(after=60)
        rig.tick(after=5)
    [(_, text)] = rig.said()
    assert 'Errno' not in text and "isn't answering" not in text
    assert caplog.text.count('[Errno -2] Name or service not known') == 1      # once, not at every look


def test_a_new_reason_goes_in_the_log_too(caplog):
    rig = Rig(pi=PiReports())
    with caplog.at_level(logging.WARNING, logger='desktalk.core.chat.workers.lens'):
        rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason='first reason')
        rig.tick()
        rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason='second reason')
        rig.tick(after=5)
    assert 'first reason' in caplog.text and 'second reason' in caplog.text


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
    assert kind == 'problem' and "isn't finding the objects" in text


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


# ── what the health checks find ──
def test_no_new_detections_for_a_while_is_said_then_serious_then_thanks():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now())
    rig.tick()
    rig.detecting = False
    rig.tick(after=299)
    assert rig.said() == []
    rig.tick(after=1)
    rig.tick(after=600)
    assert rig.said() == [('problem', WORDS.no_detections.format(minutes=5)),
                          ('problem', WORDS.no_detections_long.format(minutes=15))]
    rig.detecting = True
    rig.tick(after=2)
    rig.tick(after=5)
    assert rig.said()[-1] == ('fixed', WORDS.pi_fixed.detections)
    assert [n.key for n in rig.bus.of_type(CameraNotice)] == ['detections'] * 3


def test_no_detections_are_not_said_on_top_of_the_pis_detector_being_off():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=NO_CONTAINER)
    rig.detecting = False
    rig.tick()
    rig.tick(after=60)
    rig.tick(after=300)
    assert [n.key for n in rig.bus.of_type(CameraNotice)] == ['pi_detector']


def test_no_detections_are_not_said_when_detection_was_turned_off_on_purpose():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now(), detector=DetectorState.OFF, reason=DETECTOR_TURNED_OFF)
    rig.detecting = False
    rig.tick()
    rig.tick(after=1000)
    assert rig.said() == []


def test_no_thanks_for_detections_when_the_camera_went_away_instead():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now())
    rig.detecting = False
    rig.tick()
    rig.tick(after=300)
    rig.source.connected = False
    rig.tick(after=2)
    rig.source.connected = True
    rig.tick(after=2)
    rig.tick(after=10)
    assert [kind for kind, _ in rig.said()] == ['problem']


def test_no_detections_with_any_camera():
    rig = Rig(kind='webcam')
    rig.detecting = False
    rig.tick()
    rig.tick(after=300)
    assert rig.said() == [('problem', WORDS.no_detections.format(minutes=5))]


def test_a_card_getting_full_is_said_and_then_serious():
    rig = Rig(free_mb=1500.0)
    rig.tick()
    rig.disk.free = 400.0
    rig.tick(after=2)
    assert rig.said() == [('problem', WORDS.card_low.pi.format(free_mb=1500.0)),
                          ('problem', WORDS.card_full.pi.format(free_mb=400.0))]
    assert rig.notifier.sent[0]['notify'] == 'warning'
    rig.disk.free = 5000.0
    rig.tick(after=2)
    rig.tick(after=5)
    assert rig.said()[-1] == ('fixed', WORDS.pi_fixed.card.pi)


def test_a_card_hovering_around_the_warning_level_is_said_once_until_the_repeat_time():
    rig = Rig(free_mb=1990.0)
    for _ in range(5):
        for _ in range(10):
            rig.disk.free = 1990.0
            rig.tick(after=2)
        rig.disk.free = 2010.0
        rig.tick(after=2)
    assert rig.said() == [('problem', WORDS.card_low.pi.format(free_mb=1990.0))]


def test_the_card_warning_names_the_pis_sd_card_only_when_the_pi_is_the_camera():
    """The app runs on the Pi when the Pi is the camera; with a webcam or the phone it's this computer's disk."""
    for kind in ('pi', 'webcam', 'phone'):
        rig = Rig(kind=kind, free_mb=1500.0)
        rig.tick()
        rig.disk.free = 400.0
        rig.tick(after=2)
        rig.disk.free = 5000.0
        rig.tick(after=2)
        rig.tick(after=5)
        texts = [text for _, text in rig.said()]
        assert len(texts) == 3
        if kind == 'pi':
            assert all('SD card' in text for text in texts)
        else:
            assert not any('SD card' in text or 'Raspberry Pi' in text for text in texts)
            assert all("this computer's disk" in text.lower() for text in texts)


def test_room_mode_uses_the_room_words_for_the_card_check():
    rig = Rig(saving=Saving.ROOM, free_mb=250.0)
    rig.tick()
    rig.disk.free = 80.0
    rig.tick(after=2)
    rig.disk.free = 5000.0
    rig.tick(after=2)
    rig.tick(after=5)
    assert rig.said() == [('problem', WORDS.room_low.format(free_mb=250.0)),
                          ('problem', WORDS.room_full.format(free_mb=80.0)),
                          ('fixed', WORDS.pi_fixed.room)]


def test_the_rest_of_the_card_running_low_says_saving_goes_on():
    rig = Rig(saving=Saving.ROOM, rest_mb=800.0)
    rig.tick()
    rig.rest.free = 200.0
    rig.tick(after=2)
    low, full = [text for _, text in rig.said()]
    assert low == WORDS.card_rest_low.format(free_mb=800.0) and '800' in low
    assert full == WORDS.card_rest_full.format(free_mb=200.0) and '200' in full
    for text in (low, full):
        assert 'still saving' in text and 'updates' in text and 'logs' in text
    rig.rest.free = 5000.0
    rig.tick(after=2)
    rig.tick(after=5)
    assert rig.said()[-1] == ('fixed', WORDS.pi_fixed.card_rest)


def test_the_rest_of_the_card_is_not_watched_in_plain_mode():
    rig = Rig(rest_mb=10.0)
    rig.tick()
    assert rig.said() == []


def test_saving_off_is_said_once_and_ranked_serious():
    rig = Rig(saving=Saving.OFF)
    rig.tick()
    rig.tick(after=2)
    assert rig.said() == [('problem', WORDS.saving_off)]
    assert 'nothing is being saved' in WORDS.saving_off.lower()
    assert "If Desk Talk's space is missing" in WORDS.saving_off
    assert "raspberry_pi/README.md" in WORDS.saving_off
    assert rig.notifier.sent[0]['notify'] == 'warning'


def test_plain_mode_words_are_unchanged():
    rig = Rig(free_mb=1500.0)
    rig.tick()
    rig.disk.free = 400.0
    rig.tick(after=2)
    assert rig.said() == [('problem', WORDS.card_low.pi.format(free_mb=1500.0)),
                          ('problem', WORDS.card_full.pi.format(free_mb=400.0))]


def test_a_heartbeat_that_stops_while_the_pictures_still_come_is_said():
    rig = Rig(pi=PiReports())
    rig.pi.picture(rig.clock.now())
    rig.pi.heartbeat(temp=50.0)
    rig.tick()
    rig.tick(after=89)
    rig.pi.picture(rig.clock.now())
    assert rig.said() == []
    rig.tick(after=1)
    assert rig.said() == [('problem', WORDS.pi_heartbeat.format(seconds=90))]
    rig.pi.heartbeat(temp=50.0)
    rig.tick(after=2)
    rig.tick(after=5)
    assert rig.said()[-1] == ('fixed', WORDS.pi_fixed.pi_heartbeat)


def test_a_heartbeat_is_not_missed_while_the_pictures_have_stopped_too():
    """The camera check already says the pictures stopped."""
    rig = Rig(pi=PiReports())
    rig.pi.heartbeat(temp=50.0)
    rig.source.connected = False
    rig.tick()
    rig.tick(after=200)
    assert rig.said() == []


# ── a warning said on purpose (the developer page's Demo tab) ──
def test_say_problem_goes_out_like_a_found_problem():
    """The Demo tab's pretend "camera covered" goes the same way as a real one: the chat (with the warning
    pop-up mark) and a CameraNotice in the event log."""
    rig = Rig()
    assert isinstance(rig.lens, CameraWarnings)
    rig.lens.say_problem('covered', 'Everything just went black.', rig.clock.now())
    assert [(m['character'], m['text'], m['lens'], m['toast'], m['notify']) for m in rig.notifier.sent] == [
        ('lens', 'Everything just went black.', 'problem', True, 'warning')]
    assert [(n.key, n.kind, n.text) for n in rig.bus.of_type(CameraNotice)] == [
        ('covered', 'problem', 'Everything just went black.')]
