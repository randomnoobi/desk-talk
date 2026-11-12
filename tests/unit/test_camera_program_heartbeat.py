"""raspberry_pi/camera/heartbeat.py: telling the app how the Pi is doing on a timer of its own, even when no pictures
go out."""

import logging

import pytest
from test_camera_program_fakes import KEY, FakeComputer

from desktalk_shared.wire import PiHealth
from raspberry_pi.camera.heartbeat import Heartbeat

EVERY = 30
HEALTH = PiHealth(camera='picamera2', photos_stored=12, disk_free_mb=5000, cpu_temp_c=61.5, throttled='0x0')


@pytest.fixture
def computer():
    with FakeComputer() as c:
        yield c


def make_heartbeat(computer, key=KEY, read=lambda: HEALTH):
    return Heartbeat(computer.url, key, read, every_sec=EVERY, timeout_sec=2, error_text_chars=200)


def test_sends_how_the_pi_is_doing_with_the_pairing_key(computer):
    beat = make_heartbeat(computer)
    assert beat.tick() == EVERY
    assert computer.heartbeats == [HEALTH]
    assert beat.sent == 1


def test_reads_the_health_afresh_each_time(computer):
    temps = iter([50.0, 70.0])
    beat = make_heartbeat(computer, read=lambda: PiHealth(cpu_temp_c=next(temps)))
    beat.tick()
    beat.tick()
    assert [h.cpu_temp_c for h in computer.heartbeats] == [50.0, 70.0]


def test_a_wrong_key_is_logged_once_and_it_keeps_going(computer, caplog):
    beat = make_heartbeat(computer, key='wrong')
    assert beat.tick() == EVERY
    assert beat.tick() == EVERY
    assert beat.failed == 2 and computer.heartbeats == []
    assert caplog.text.count('Pairing key does not match') == 1


def test_an_app_that_is_away_is_logged_and_tried_again_next_time(computer, caplog):
    caplog.set_level(logging.INFO)
    beat = make_heartbeat(computer)
    computer.away = True
    assert beat.tick() == EVERY
    assert 'Cannot reach the app' in beat.last_error
    computer.away = False
    beat.tick()
    assert computer.heartbeats == [HEALTH]
    assert 'reaching' in caplog.text
