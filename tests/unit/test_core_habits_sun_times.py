"""Sunrise and sunset times for the light habits (desktalk/core/habits/sun_times.py)."""

import calendar
import time

import pytest

from desktalk.core.habits.sun_times import sun_times
from tests.fakes import FakeClock

LOS_ANGELES = FakeClock(utc_offset_hours=-7)        # summer time there, in June and October


@pytest.fixture
def los_angeles(monkeypatch):
    """Run the test in Los Angeles time, then put the computer's time zone back."""
    monkeypatch.setenv('TZ', 'America/Los_Angeles')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def local(year, month, day, hour, minute=0):
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


def test_sunset_for_los_angeles_in_october(los_angeles):
    now = local(2026, 10, 18, 12)
    t = sun_times(now, 33.88, -118.40, '18:30', LOS_ANGELES)
    sunset = time.localtime(t['sunset'])
    sunrise = time.localtime(t['sunrise'])
    assert (18, 5) <= (sunset.tm_hour, sunset.tm_min) <= (18, 25)
    assert sunrise.tm_hour in (6, 7)
    assert t['estimated'] is False


def test_sun_times_stay_on_the_same_local_day(los_angeles):
    now = local(2026, 10, 18, 23, 30)
    t = sun_times(now, 33.88, -118.40, '18:30', LOS_ANGELES)
    assert time.localtime(t['sunset']).tm_mday == 18
    assert time.localtime(t['sunrise']).tm_mday == 18


def test_no_place_set_uses_the_fallback_sunset(los_angeles):
    now = local(2026, 10, 18, 12)
    t = sun_times(now, None, None, '19:15', LOS_ANGELES)
    assert t == {'sunrise': None, 'sunset': local(2026, 10, 18, 19, 15), 'estimated': True}


def test_no_sunset_in_the_arctic_summer_uses_the_fallback(los_angeles):
    now = local(2026, 6, 21, 12)
    t = sun_times(now, 80.0, -118.40, '19:15', LOS_ANGELES)
    assert t['sunset'] == local(2026, 6, 21, 19, 15)
    assert t['sunrise'] is None and t['estimated'] is True


def test_the_day_comes_from_the_clock():
    """23:30 on 18 October in Los Angeles is already the 19th in UTC: the clock decides which day it is."""
    now = 1_792_391_400.0                     # 2026-10-19 06:30 UTC
    la = sun_times(now, 33.88, -118.40, '18:30', LOS_ANGELES)
    utc = sun_times(now, 33.88, -118.40, '18:30', FakeClock())
    assert LOS_ANGELES.local_day(la['sunset']) == '2026-10-18'
    assert FakeClock().local_day(utc['sunset']) == '2026-10-19'


def test_the_fallback_sunset_is_on_the_clocks_time():
    tokyo = FakeClock(utc_offset_hours=9)
    noon_there = calendar.timegm((2026, 10, 18, 3, 0, 0, 0, 0, 0))
    assert sun_times(noon_there, None, None, '19:15', tokyo)['sunset'] == \
        calendar.timegm((2026, 10, 18, 19, 15, 0, 0, 0, 0)) - 9 * 3600
