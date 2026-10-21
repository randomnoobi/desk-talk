"""Sunrise and sunset times for the light habits (desktalk/core/habits/sun_times.py)."""

import time

import pytest

from desktalk.core.habits.sun_times import sun_times


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
    t = sun_times(now, 33.88, -118.40, '18:30')
    sunset = time.localtime(t['sunset'])
    sunrise = time.localtime(t['sunrise'])
    assert (18, 5) <= (sunset.tm_hour, sunset.tm_min) <= (18, 25)
    assert sunrise.tm_hour in (6, 7)
    assert t['estimated'] is False


def test_sun_times_stay_on_the_same_local_day(los_angeles):
    now = local(2026, 10, 18, 23, 30)
    t = sun_times(now, 33.88, -118.40, '18:30')
    assert time.localtime(t['sunset']).tm_mday == 18
    assert time.localtime(t['sunrise']).tm_mday == 18


def test_no_place_set_uses_the_fallback_sunset(los_angeles):
    now = local(2026, 10, 18, 12)
    t = sun_times(now, None, None, '19:15')
    assert t == {'sunrise': None, 'sunset': local(2026, 10, 18, 19, 15), 'estimated': True}


def test_no_sunset_in_the_arctic_summer_uses_the_fallback(los_angeles):
    now = local(2026, 6, 21, 12)
    t = sun_times(now, 80.0, -118.40, '19:15')
    assert t['sunset'] == local(2026, 6, 21, 19, 15)
    assert t['sunrise'] is None and t['estimated'] is True
