"""When a Pi's weekly report is due, in that Pi's own time zone.

A report is due every week at one weekday and hour (settings → reports.day, reports.hour). The week it covers is
the 7 days up to that moment, counted on the Pi's wall clock, so a week with the clocks changing is an hour longer or
shorter than 7 * 24 hours. Times are seconds since 1970; a week's name is its first whole day, 'YYYY-MM-DD'.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

__all__ = ['DAYS_IN_WEEK', 'due_for_week', 'latest_due', 'week_of', 'zone_name', 'zone_of']

log = logging.getLogger(__name__)
_warned: set[str] = set()      # the unknown names already warned about, so a Pi's odd setting is not logged every round

MAX_NAME_CHARS = 60           # how much of an odd time zone name a log line shows
DAYS_IN_WEEK = 7
LOOKAHEAD_DAYS = 8            # from a week's first whole day: past the end of that week and not into the next one


def zone_of(name: str) -> tzinfo:
    """The time zone with this name. A name the server doesn't know (or a strange one) gives UTC and a warning (the
    first time), so one Pi's odd setting never stops anything."""
    try:
        return ZoneInfo(name)
    except (LookupError, ValueError, OSError):          # not found, not a valid name, or a folder instead of a zone
        if name not in _warned:
            _warned.add(name)
            log.warning('Unknown time zone %r: using UTC for it', name[:MAX_NAME_CHARS])
        return UTC


def _moment(day: date, hour: int, zone: tzinfo) -> float:
    return datetime.combine(day, time(hour), tzinfo=zone).timestamp()


def latest_due(now: float, zone: tzinfo, weekday: int, hour: int) -> float:
    """The newest moment, at or before `now`, that is `weekday` (Monday = 0) at `hour` o'clock in `zone`."""
    today = datetime.fromtimestamp(now, zone).date()
    day = today - timedelta(days=(today.weekday() - weekday) % DAYS_IN_WEEK)
    if _moment(day, hour, zone) > now:
        day -= timedelta(days=DAYS_IN_WEEK)
    return _moment(day, hour, zone)


def week_of(due: float, zone: tzinfo, weekday: int, hour: int) -> tuple[float, float, str]:
    """(start, end, first_day) of the week that ends at `due`: the due moment before it, `due`, and the first whole day
    of the week (the day after the start, unless the start is exactly midnight). The start is the previous due moment
    itself, so two weeks always touch, even when the clocks change or `hour` is an hour the clocks skip."""
    start = datetime.fromtimestamp(latest_due(due - 1, zone, weekday, hour), zone)
    first_day = start.date() if start.time() == time(0) else start.date() + timedelta(days=1)
    return start.timestamp(), due, first_day.isoformat()


def zone_name(zone: tzinfo) -> str:
    """The name of a zone zone_of() gave: 'UTC' for the one it falls back to."""
    return getattr(zone, 'key', None) or 'UTC'


def due_for_week(first_day: str, zone: tzinfo, weekday: int, hour: int) -> float:
    """The due moment of the week named `first_day` (the opposite of week_of's name)."""
    after = _moment(date.fromisoformat(first_day) + timedelta(days=LOOKAHEAD_DAYS), 0, zone)
    return latest_due(after, zone, weekday, hour)
