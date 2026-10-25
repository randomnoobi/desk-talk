from __future__ import annotations

import pytest

from desktalk.runtime import SystemClock
from tests.contracts.clock_contract import ClockContract
from tests.fakes import FakeClock


class TestFakeClock(ClockContract):
    @pytest.fixture
    def clock(self):
        return FakeClock()


class TestFakeClockAwayFromUtc(ClockContract):
    @pytest.fixture
    def clock(self):
        return FakeClock(utc_offset_hours=5.5)


class TestSystemClock(ClockContract):
    @pytest.fixture
    def clock(self):
        return SystemClock()


def test_fake_clock_moves_only_when_told():
    clock = FakeClock(start=1_700_000_000.0)
    assert clock.now() == clock.now() == 1_700_000_000.0
    clock.advance(5)
    assert clock.now() == 1_700_000_005.0
    clock.sleep(2)
    assert clock.now() == 1_700_000_007.0
    assert clock.slept == [2]


def test_fake_clock_tells_the_hour_and_day_in_a_fixed_time_zone():
    utc = FakeClock()
    assert (utc.local_hour(0), utc.local_day(0)) == (0, '1970-01-01')
    assert utc.local_hour(5 * 3600 + 59) == 5
    los_angeles = FakeClock(utc_offset_hours=-7)
    assert (los_angeles.local_hour(0), los_angeles.local_day(0)) == (17, '1969-12-31')


def test_system_clock_uses_this_computers_time_zone():
    import time
    clock = SystemClock()
    ts = 1_760_000_000.0
    assert clock.local_hour(ts) == time.localtime(ts).tm_hour
    assert clock.local_day(ts) == time.strftime('%Y-%m-%d', time.localtime(ts))


def test_fake_clock_tells_its_utc_offset():
    assert FakeClock().utc_offset_sec(0) == 0
    assert FakeClock(utc_offset_hours=-7).utc_offset_sec(1_760_000_000.0) == -7 * 3600


def test_system_clock_tells_this_computers_utc_offset():
    import time
    ts = 1_760_000_000.0
    assert SystemClock().utc_offset_sec(ts) == time.localtime(ts).tm_gmtoff
