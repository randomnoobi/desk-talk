"""The local-time helpers in desktalk/util.py: given a UTC offset they are the same on every computer, and
without one they use this computer's time zone, as the running app does."""

import calendar
import time

from desktalk.util import at_time_today, clock_text, format_local_ts, local_day

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
    assert at_time_today('07:30', THREE_PM_UTC, utc_offset_sec=0) == calendar.timegm((2026, 10, 21, 7, 30, 0, 0, 0, 0))
    seven_thirty_la = calendar.timegm((2026, 10, 21, 7, 30, 0, 0, 0, 0)) + 7 * HOUR
    assert at_time_today('07:30', THREE_PM_UTC, utc_offset_sec=-7 * HOUR) == seven_thirty_la


def test_without_an_offset_this_computers_time_zone_is_used():
    assert format_local_ts(THREE_PM_UTC) == time.strftime('%b %d, %Y · %I:%M %p', time.localtime(THREE_PM_UTC))
    assert clock_text(THREE_PM_UTC) == time.strftime('%I:%M %p', time.localtime(THREE_PM_UTC)).lstrip('0')
    assert local_day(THREE_PM_UTC) == time.strftime('%Y-%m-%d', time.localtime(THREE_PM_UTC))
    lt = time.localtime(THREE_PM_UTC)
    assert at_time_today('07:30', THREE_PM_UTC) == time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 7, 30, 0, 0, 0, -1))
