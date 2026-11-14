"""desktalk/core/health/checks.py: each measure checked on its own (camera pictures, new detections, the Pi's
temperature, the card's free space, the Pi's heartbeat)."""

from __future__ import annotations

from desktalk.config import HealthChecksSettings, PiSettings
from desktalk.core.health import HealthChecks
from desktalk.domain import CheckLevel, PiHealth, Saving
from desktalk.ports import HealthCheck
from tests.fakes import FakeClock, FakeDetectionTimes, FakeDiskSpace, FakeFrameSource, FakePi

SETTINGS = HealthChecksSettings.model_construct(card_warning_below_mb=2000.0, card_serious_below_mb=500.0,
                                                room_warning_below_mb=300.0, room_serious_below_mb=100.0,
                                                card_rest_warning_below_mb=2000.0, card_rest_serious_below_mb=500.0,
                                                no_detections_warning_sec=300.0, no_detections_serious_sec=900.0,
                                                pi_heartbeat_late_sec=90.0)
PI = PiSettings.model_construct(hot_warning_c=75.0, hot_serious_c=80.0)
OK, WARNING, SERIOUS, UNKNOWN = CheckLevel.OK, CheckLevel.WARNING, CheckLevel.SERIOUS, CheckLevel.UNKNOWN


class Rig:
    def __init__(self, *, pi: bool = True, free_mb: float | None = 10_000.0, saving: Saving = Saving.PLAIN,
                 rest_mb: float | None = 10_000.0):
        self.clock = FakeClock()
        self.source = FakeFrameSource('pi' if pi else 'webcam', clock=self.clock)
        self.source.connected = True
        self.detections = FakeDetectionTimes()
        self.disk = FakeDiskSpace(free_mb)
        self.card = FakeDiskSpace(rest_mb)
        self.pi = FakePi(self.clock)
        self.checks = HealthChecks(self.source, self.detections, self.disk, SETTINGS, PI, clock=self.clock,
                                   pi=self.pi if pi else None, saving=saving, card=self.card)

    def found_now(self) -> None:
        self.detections.found_at = self.clock.monotonic()

    def measure(self, key: str):
        return {m.key: m for m in self.checks.measures()}[key]

    def level(self, key: str) -> CheckLevel:
        return self.measure(key).level


def test_it_is_a_health_check_with_every_measure_once():
    rig = Rig()
    assert isinstance(rig.checks, HealthCheck)
    assert [m.key for m in rig.checks.measures()] == ['camera', 'detections', 'pi_temperature', 'card',
                                                       'pi_heartbeat', 'card_rest', 'saving']


# ── camera pictures ──
def test_the_camera_is_fine_while_its_pictures_arrive_and_serious_when_they_stop():
    rig = Rig()
    assert rig.level('camera') is OK
    rig.source.connected = False
    assert rig.level('camera') is SERIOUS


# ── new detections ──
def test_detections_are_fine_while_pictures_go_through_detection():
    rig = Rig()
    rig.found_now()
    rig.clock.advance(299)
    m = rig.measure('detections')
    assert (m.level, m.value, m.unit) == (OK, 299.0, 's')


def test_no_new_detections_for_a_while_is_a_warning_then_serious():
    rig = Rig()
    rig.found_now()
    rig.clock.advance(300)
    assert rig.level('detections') is WARNING
    rig.clock.advance(600)
    assert rig.level('detections') is SERIOUS


def test_no_detection_ever_counts_from_when_the_camera_connected():
    rig = Rig()
    rig.checks.measures()                   # the camera is seen connected now
    rig.clock.advance(299)
    assert rig.level('detections') is OK
    rig.clock.advance(1)
    assert rig.level('detections') is WARNING


def test_detections_are_not_judged_while_the_camera_is_away():
    rig = Rig()
    rig.found_now()
    rig.clock.advance(1000)
    rig.source.connected = False
    m = rig.measure('detections')
    assert (m.level, m.value) == (UNKNOWN, None)


def test_a_camera_that_comes_back_gets_the_whole_wait_again():
    rig = Rig()
    rig.found_now()
    rig.source.connected = False
    rig.checks.measures()
    rig.clock.advance(1000)
    rig.source.connected = True
    rig.checks.measures()                   # back now
    rig.clock.advance(299)
    assert rig.level('detections') is OK


# ── the Pi's temperature ──
def test_the_pis_temperature_has_a_warning_and_a_serious_level():
    rig = Rig()
    for temp, level in ((60.0, OK), (75.0, WARNING), (79.9, WARNING), (80.0, SERIOUS)):
        rig.pi.heartbeat(PiHealth(cpu_temp_c=temp))
        m = rig.measure('pi_temperature')
        assert (m.level, m.value, m.unit) == (level, temp, '°C')


def test_a_temperature_too_old_to_go_by_is_unknown():
    rig = Rig()
    rig.pi.heartbeat(PiHealth(cpu_temp_c=85.0))
    rig.clock.advance(90)
    m = rig.measure('pi_temperature')
    assert (m.level, m.value) == (UNKNOWN, 85.0)


def test_no_temperature_without_a_pi_or_before_it_says_one():
    assert Rig(pi=False).level('pi_temperature') is UNKNOWN
    rig = Rig()
    assert rig.level('pi_temperature') is UNKNOWN
    rig.pi.heartbeat(PiHealth(camera='usb'))
    assert rig.level('pi_temperature') is UNKNOWN


# ── the card's free space ──
def test_the_cards_free_space_has_a_warning_and_a_serious_level():
    for free, level in ((5000.0, OK), (2000.0, OK), (1999.0, WARNING), (500.0, WARNING), (499.0, SERIOUS)):
        m = Rig(free_mb=free).measure('card')
        assert (m.level, m.value, m.unit) == (level, free, 'MB')


def test_free_space_that_cant_be_read_is_unknown():
    assert Rig(free_mb=None).level('card') is UNKNOWN


def test_the_card_is_checked_with_any_camera():
    assert Rig(pi=False, free_mb=100.0).level('card') is SERIOUS


# ── the Pi's heartbeat ──
def test_a_heartbeat_that_stops_is_a_warning():
    rig = Rig()
    rig.pi.heartbeat(PiHealth())
    rig.clock.advance(89)
    m = rig.measure('pi_heartbeat')
    assert (m.level, m.value, m.unit) == (OK, 89.0, 's')
    rig.clock.advance(1)
    assert rig.level('pi_heartbeat') is WARNING


def test_no_heartbeat_yet_is_unknown():
    """A Pi with an older camera program never sends one, so there is nothing to miss."""
    assert Rig().level('pi_heartbeat') is UNKNOWN
    assert Rig(pi=False).level('pi_heartbeat') is UNKNOWN


# ── the saving modes ──
def test_plain_mode_measures_the_card_as_before():
    rig = Rig(free_mb=1500.0)
    assert rig.level('card') is WARNING
    assert rig.level('card_rest') is UNKNOWN
    assert rig.level('saving') is OK


def test_room_mode_measures_the_room_with_its_own_levels():
    for free, level in ((400.0, OK), (250.0, WARNING), (50.0, SERIOUS)):
        m = Rig(free_mb=free, saving=Saving.ROOM).measure('card')
        assert (m.level, m.value, m.unit) == (level, free, 'MB')


def test_room_mode_measures_the_rest_of_the_card():
    for free, level in ((1500.0, WARNING), (400.0, SERIOUS), (5000.0, OK)):
        m = Rig(saving=Saving.ROOM, rest_mb=free).measure('card_rest')
        assert (m.level, m.value, m.unit) == (level, free, 'MB')


def test_off_mode_says_nothing_is_saved():
    rig = Rig(saving=Saving.OFF, rest_mb=1500.0)
    assert rig.level('saving') is SERIOUS
    assert rig.level('card') is UNKNOWN
    assert rig.level('card_rest') is WARNING


def test_unreadable_rest_of_the_card_is_unknown():
    assert Rig(saving=Saving.ROOM, rest_mb=None).level('card_rest') is UNKNOWN


def test_the_rest_of_the_card_is_unknown_without_a_card_to_look_at():
    rig = Rig(saving=Saving.ROOM)
    checks = HealthChecks(rig.source, rig.detections, rig.disk, SETTINGS, PI, clock=rig.clock, saving=Saving.ROOM)
    assert {m.key: m for m in checks.measures()}['card_rest'].level is UNKNOWN
