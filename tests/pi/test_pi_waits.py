"""The waiting helpers in tests/pi/conftest.py, with no Pi: these run with the fast tests."""

from __future__ import annotations

from typing import Any

import pytest

from tests.pi.conftest import Pi, camera_log_command, status_line_fresh_sec, status_line_wait_sec, wait_for_status_lines
from tests.pi.hand_test_rules import parse_status_lines
from tests.pi.test_real_pi import latest_camera_status, next_camera_status

STATUS = ('2026-11-24T05:26:03.5Z Pictures taken 90, sent 89, failed 0, skipped 0; detector ok 80, busy 9, off 0; '
          'stored 9; 250 MB free; 56.2 °C')


def test_waits_for_the_first_status_line_of_a_camera_that_just_started():
    logs = iter(['', '2026-11-24T05:26:01.0Z Camera ready', STATUS])
    reads = []

    def read_log() -> str:
        reads.append(1)
        return next(logs)
    lines = wait_for_status_lines(read_log, parse_status_lines, timeout=10, every=0)
    assert [line.taken for line in lines] == [90]
    assert len(reads) == 3


def test_says_what_it_waited_for_when_no_status_line_comes():
    with pytest.raises(AssertionError, match="a status line in the camera's log: still not so after 0 s"):
        wait_for_status_lines(lambda: 'Camera ready', parse_status_lines, timeout=0, every=0)


def test_the_timeout_shows_the_end_of_the_last_log_read():
    log = '\n'.join(f'line {n}' for n in range(1, 11))
    with pytest.raises(AssertionError) as raised:
        wait_for_status_lines(lambda: log, parse_status_lines, timeout=0, every=0)
    message = str(raised.value)
    assert message.startswith("a status line in the camera's log: still not so after 0 s")
    assert 'line 10' in message and 'line 6' in message
    assert 'line 5\n' not in message and not message.endswith('line 5')


def test_the_wait_is_two_status_times_and_the_warm_up():
    """One status time for the line itself, one more as room for the camera program to start."""
    assert status_line_wait_sec({'status_every_sec': 30, 'camera_warmup_sec': 1}) == 61


def test_a_status_line_is_fresh_for_three_status_times():
    """Two status times, and one more as room."""
    assert status_line_fresh_sec({'status_every_sec': 30, 'camera_warmup_sec': 1}) == 90


STARTED = '2026-11-24T05:25:58.123456789Z'


def test_looks_only_since_the_camera_started_when_the_clock_is_ahead():
    command = camera_log_command('desktalk-camera-1', STARTED, '2026-11-24T05:26:03', 40)
    assert command == ("docker logs -t --since 2026-11-24T05:25:58.123456789Z --tail 40 desktalk-camera-1 2>&1")


def test_looks_only_since_the_camera_started_in_the_same_second():
    """Docker trims the fraction's trailing zeros, so the two are compared at whole seconds."""
    command = camera_log_command('desktalk-camera-1', '2026-11-24T05:25:58Z', '2026-11-24T05:25:58', 40)
    assert '--since' in command


def test_leaves_out_since_when_the_clock_was_set_back():
    """After the clock is set back, every new line is stamped before the camera started, and `--since` hides them."""
    command = camera_log_command('desktalk-camera-1', STARTED, '1990-01-01T00:00:10', 40)
    assert command == 'docker logs -t --tail 40 desktalk-camera-1 2>&1'


class FakePi(Pi):
    """A Pi with canned answers and no SSH."""

    def __init__(self, camera: dict[str, Any], logs: list[str]):
        super().__init__('nowhere', '~/desk-talk/raspberry_pi')
        self.camera = camera
        self.logs = logs
        self.commands: list[str] = []
        self.settings_asked = 0

    def state(self, service: str) -> dict[str, Any]:
        return self.camera

    def run(self, command: str, **kwargs: Any) -> str:
        self.commands.append(command)
        if command.startswith('date'):
            return '2026-11-24T05:26:03\n'
        return self.logs.pop(0) if len(self.logs) > 1 else self.logs[0]

    def settings(self) -> dict[str, Any]:
        self.settings_asked += 1
        return {'pi': {'status_every_sec': 0, 'camera_warmup_sec': 0}}


def test_a_missing_camera_container_says_so():
    pi = FakePi({'Running': False, 'Missing': True}, [''])
    with pytest.raises(AssertionError, match="the camera's container is missing"):
        pi.camera_status_lines(parse_status_lines)


def test_does_not_ask_for_the_settings_when_the_first_read_has_a_status_line():
    pi = FakePi({'Running': True, 'StartedAt': STARTED}, [STATUS])
    assert [line.taken for line in pi.camera_status_lines(parse_status_lines)] == [90]
    assert pi.settings_asked == 0
    assert any('--since' in command for command in pi.commands)


def test_asks_for_the_settings_and_waits_when_the_first_read_has_none():
    pi = FakePi({'Running': True, 'StartedAt': STARTED}, ['', STATUS])
    assert [line.taken for line in pi.camera_status_lines(parse_status_lines)] == [90]
    assert pi.settings_asked == 1


def test_waits_for_a_status_line_newer_than_the_one_before():
    later = STATUS.replace('taken 90', 'taken 180')
    pi = FakePi({'Running': True, 'StartedAt': STARTED}, [STATUS, later])
    before = latest_camera_status(pi)
    assert before[0] == 90
    assert next_camera_status(pi, before, every=0)[0] == 180


def test_a_camera_that_started_again_never_gives_a_newer_status_line():
    """Its count starts from 0 again, so the line after it isn't newer, and the wait says so."""
    pi = FakePi({'Running': True, 'StartedAt': STARTED}, [STATUS.replace('taken 90', 'taken 3')])
    with pytest.raises(AssertionError, match='a status line newer than the one with 90 pictures taken'):
        next_camera_status(pi, (90, 89, 0, 0, 80, 9, 0), every=0)
