"""When a Pi's weekly report is due, in that Pi's own time zone (desktalk_cloud.report_times)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from desktalk_cloud.report_times import due_for_week, latest_due, week_of, zone_name, zone_of

SUNDAY = 6
HOUR = 3600
DAY = 86400


def utc(year, month, day, hour=0, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp()


def utc_offset(zone) -> float:
    offset = datetime.fromtimestamp(0, zone).utcoffset()
    assert offset is not None
    return offset.total_seconds()


def test_due_is_sunday_20_local():
    los_angeles = zone_of('America/Los_Angeles')
    due = utc(2026, 10, 26, 3, 0)                                    # Sunday 25 Oct, 20:00 in Los Angeles (UTC-7)
    assert latest_due(due, los_angeles, SUNDAY, 20) == due           # exactly at the moment counts
    assert latest_due(due + 1, los_angeles, SUNDAY, 20) == due
    assert latest_due(due - 1, los_angeles, SUNDAY, 20) == due - 7 * DAY      # a second early: last week's
    assert latest_due(utc(2026, 10, 31, 12), los_angeles, SUNDAY, 20) == due
    tokyo = zone_of('Asia/Tokyo')
    assert latest_due(utc(2026, 10, 25, 11, 0), tokyo, SUNDAY, 20) == utc(2026, 10, 25, 11, 0)   # 20:00 in Tokyo


def test_week_across_clocks_changing():
    los_angeles = zone_of('America/Los_Angeles')
    # Clocks go back on Sunday 1 Nov 2026 at 02:00, so this week is an hour longer than 7 days.
    due = latest_due(utc(2026, 11, 2, 5), los_angeles, SUNDAY, 20)
    assert due == utc(2026, 11, 2, 4, 0)                             # Sunday 1 Nov, 20:00 PST (UTC-8)
    start, end, first_day = week_of(due, los_angeles, SUNDAY, 20)
    assert end == due
    assert start == utc(2026, 10, 26, 3, 0)                          # Sunday 25 Oct, 20:00 PDT (UTC-7)
    assert end - start == 7 * DAY + HOUR
    assert first_day == '2026-10-26'                                 # the first whole day: a Monday

    # Clocks go forward on Sunday 8 Mar 2026: that week is an hour shorter.
    due = latest_due(utc(2026, 3, 9, 4), los_angeles, SUNDAY, 20)
    start, end, first_day = week_of(due, los_angeles, SUNDAY, 20)
    assert end - start == 7 * DAY - HOUR
    assert first_day == '2026-03-02'


def test_week_at_midnight_starts_on_that_day():
    start, end, first_day = week_of(utc(2026, 11, 1, 0), zone_of('UTC'), SUNDAY, 0)
    assert first_day == '2026-10-25'                                 # the week starts at 00:00, so that day is whole
    assert end - start == 7 * DAY


def test_due_once_per_week():
    zone = zone_of('America/Los_Angeles')
    first = utc(2026, 10, 1)
    dues = [latest_due(first + step * HOUR, zone, SUNDAY, 20) for step in range(0, 24 * 21)]
    changes = [i for i in range(1, len(dues)) if dues[i] != dues[i - 1]]
    assert len(changes) == 3                                         # three weeks of hours: three new due moments
    distinct = sorted(set(dues))
    assert all(abs(b - a - 7 * DAY) <= HOUR for a, b in zip(distinct, distinct[1:], strict=False))
    # Two different due moments never share a week_of() name, so a week can't be written twice.
    assert len({week_of(due, zone, SUNDAY, 20)[2] for due in distinct}) == len(distinct)


def test_the_week_a_report_names_gives_its_end_back():
    zone = zone_of('Europe/London')
    for now in (utc(2026, 3, 30, 12), utc(2026, 10, 27, 12), utc(2026, 12, 25, 9)):
        due = latest_due(now, zone, SUNDAY, 20)
        assert due_for_week(week_of(due, zone, SUNDAY, 20)[2], zone, SUNDAY, 20) == due


def test_unknown_zone_uses_utc_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        zone = zone_of('Mars/Olympus_Mons')
    assert utc_offset(zone) == 0
    assert 'Mars/Olympus_Mons' in caplog.text
    assert latest_due(utc(2026, 10, 26, 3), zone, SUNDAY, 20) == utc(2026, 10, 25, 20)
    for odd in ('', '../../etc/passwd', 'America', 'a\x00b'):         # nothing a Pi sends may make this raise
        assert utc_offset(zone_of(odd)) == 0


def test_a_known_zone_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING):
        zone_of('Asia/Tokyo')
        zone_of('UTC')
    assert caplog.text == ''


def test_an_hour_the_clocks_skip_leaves_no_gap_between_two_weeks():
    los_angeles = zone_of('America/Los_Angeles')
    # 02:00 on Sunday 8 Mar 2026 does not exist in Los Angeles: this week's moment is 03:00, last week's was 02:00.
    due = latest_due(utc(2026, 3, 8, 12), los_angeles, SUNDAY, 2)
    earlier = latest_due(due - 1, los_angeles, SUNDAY, 2)
    start, end, _ = week_of(due, los_angeles, SUNDAY, 2)
    assert start == earlier and end == due                           # the weeks touch: nothing falls between them


def test_the_zone_named_is_the_zone_used():
    assert zone_name(zone_of('Asia/Tokyo')) == 'Asia/Tokyo'
    assert zone_name(zone_of('Mars/Olympus_Mons')) == 'UTC'
    assert zone_name(zone_of('')) == 'UTC'
