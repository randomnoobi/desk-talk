"""SystemClock: the one real clock, used by the Pi's camera program and by Desk Talk on this computer."""

from __future__ import annotations

import time

from desktalk.runtime.clock import SystemClock as ComputerClock
from desktalk_shared.clock import SystemClock
from raspberry_pi.camera.clock import SystemClock as PiClock


def test_the_pi_and_this_computer_use_the_same_clock():
    assert ComputerClock is SystemClock and PiClock is SystemClock


def test_it_tells_the_time_and_the_local_hour_and_day():
    clock = SystemClock()
    before = time.time()
    assert before <= clock.now() <= time.time()
    assert clock.monotonic() <= clock.monotonic()
    ts = 1_760_000_000.0
    assert clock.local_hour(ts) == time.localtime(ts).tm_hour
    assert clock.local_day(ts) == time.strftime('%Y-%m-%d', time.localtime(ts))


def test_sleep_waits():
    clock = SystemClock()
    start = clock.monotonic()
    clock.sleep(0.01)
    assert clock.monotonic() - start >= 0.01
