"""The waiting helpers in tests/pi/conftest.py, with no Pi: these run with the fast tests."""

from __future__ import annotations

import pytest

from tests.pi.conftest import status_line_wait_sec, wait_for_status_lines
from tests.pi.hand_test_rules import parse_status_lines

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


def test_the_wait_is_two_status_times_and_the_warm_up():
    """One status time for the line itself, one more as room for the camera program to start."""
    assert status_line_wait_sec({'status_every_sec': 30, 'camera_warmup_sec': 1}) == 61
