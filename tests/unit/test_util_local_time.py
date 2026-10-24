"""The local-time helpers in desktalk/util.py: given a UTC offset they are the same on every computer, and
without one they use this computer's time zone, as the running app does."""

import calendar
import time

import pytest

from desktalk.util import at_time_today, clock_text, format_local_ts, local_day
from desktalk_shared.clock import SystemClock

THREE_PM_UTC = calendar.timegm((2026, 10, 21, 15, 5, 0, 0, 0, 0))     # 21 October 2026, 15:05 UTC
HOUR = 3600


def test_format_local_ts_uses_the_offset():
    assert format_local_ts(THREE_PM_UTC, utc_offset_sec=0) == 'Oct 21, 2026 · 03:05 PM'
    assert format_local_ts(THREE_PM_UTC, utc_offset_sec=-7 * HOUR) == 'Oct 21, 2026 · 08:05 AM'
    assert format_local_ts(THREE_PM_UTC, utc_offset_sec=10 * HOUR) == 'Oct 22, 2026 · 01:05 AM'


def test_format_local_ts_without_a_time_or_with_a_broken_one():
    assert format_local_ts(0, utc_offset_sec=0) == '' and format_local_ts(None, utc_offset_sec=0) == ''
    assert format_local_ts(1e20, utc_offset_sec=0) == ''


def test_clock_text_uses_the_offset():
    assert clock_text(THREE_PM_UTC, utc_offset_sec=0) == '3:05 PM'
    assert clock_text(THREE_PM_UTC, utc_offset_sec=5.5 * HOUR) == '8:35 PM'


def test_local_day_uses_the_offset():
    assert local_day(THREE_PM_UTC, utc_offset_sec=0) == '2026-10-21'
    assert local_day(THREE_PM_UTC, utc_offset_sec=10 * HOUR) == '2026-10-22'


def test_at_time_today_uses_the_offset():
    assert at_time_today('07:30', THREE_PM_UTC, utc_offset_at=lambda ts: 0) == \
        calendar.timegm((2026, 10, 21, 7, 30, 0, 0, 0, 0))
    seven_thirty_la = calendar.timegm((2026, 10, 21, 7, 30, 0, 0, 0, 0)) + 7 * HOUR
    assert at_time_today('07:30', THREE_PM_UTC, utc_offset_at=lambda ts: -7 * HOUR) == seven_thirty_la


# New York's clocks go back an hour at 02:00 summer time on 1 November 2026 (06:00 UTC), and forward an hour at
# 02:00 winter time on 8 March 2026 (07:00 UTC).
CLOCKS_GO_BACK = calendar.timegm((2026, 11, 1, 6, 0, 0, 0, 0, 0))
CLOCKS_GO_FORWARD = calendar.timegm((2026, 3, 8, 7, 0, 0, 0, 0, 0))


def new_york_offset(ts: float) -> float:
    """Like New York's clock around those two days: summer time between them, winter time outside."""
    return -4 * HOUR if CLOCKS_GO_FORWARD <= ts < CLOCKS_GO_BACK else -5 * HOUR


def test_at_time_today_uses_the_offset_at_the_time_it_works_out():
    """On the day the clocks change, 07:00 is 07:00 on the clock after the change, not an hour off."""
    half_past_midnight_summer = calendar.timegm((2026, 11, 1, 4, 30, 0, 0, 0, 0))      # 00:30, still summer time
    seven_winter = calendar.timegm((2026, 11, 1, 12, 0, 0, 0, 0, 0))                   # 07:00 winter time
    assert at_time_today('07:00', half_past_midnight_summer, utc_offset_at=new_york_offset) == seven_winter
    half_past_midnight_winter = calendar.timegm((2026, 3, 8, 5, 30, 0, 0, 0, 0))       # 00:30, still winter time
    seven_summer = calendar.timegm((2026, 3, 8, 11, 0, 0, 0, 0, 0))                    # 07:00 summer time
    assert at_time_today('07:00', half_past_midnight_winter, utc_offset_at=new_york_offset) == seven_summer


@pytest.fixture
def in_new_york(monkeypatch):
    """This computer's clock set to New York time for the test (TZ), and put back after it."""
    if not hasattr(time, 'tzset'):
        pytest.skip('this platform cannot change its time zone for a test (no time.tzset)')
    monkeypatch.setenv('TZ', 'America/New_York')
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_at_time_today_with_the_real_clock_matches_mktime_on_change_days(in_new_york):
    clock = SystemClock()
    for now in (calendar.timegm((2026, 11, 1, 4, 30, 0, 0, 0, 0)), calendar.timegm((2026, 3, 8, 5, 30, 0, 0, 0, 0))):
        lt = time.localtime(now)
        expected = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 7, 0, 0, 0, 0, -1))
        assert at_time_today('07:00', now, utc_offset_at=clock.utc_offset_sec) == expected
        assert time.localtime(expected).tm_hour == 7


def test_without_an_offset_this_computers_time_zone_is_used():
    assert format_local_ts(THREE_PM_UTC) == time.strftime('%b %d, %Y · %I:%M %p', time.localtime(THREE_PM_UTC))
    assert clock_text(THREE_PM_UTC) == time.strftime('%I:%M %p', time.localtime(THREE_PM_UTC)).lstrip('0')
    assert local_day(THREE_PM_UTC) == time.strftime('%Y-%m-%d', time.localtime(THREE_PM_UTC))
    lt = time.localtime(THREE_PM_UTC)
    assert at_time_today('07:30', THREE_PM_UTC) == time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 7, 30, 0, 0, 0, -1))
