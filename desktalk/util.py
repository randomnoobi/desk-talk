"""Small helpers used all over the project."""

from __future__ import annotations

import calendar
import re
import time
from collections.abc import Callable


def a_or_an(word) -> str:
    return 'an' if str(word)[:1].lower() in 'aeiou' else 'a'


def format_duration(seconds) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f'{int(seconds)}s'
    if seconds < 3600:
        return f'{int(seconds / 60)}m {int(seconds % 60)}s'
    return f'{int(seconds / 3600)}h {int((seconds % 3600) / 60)}m'


def _local(ts: float, utc_offset_sec: float | None) -> time.struct_time:
    """The local time at `ts`: `utc_offset_sec` seconds from UTC, or (None) this computer's own time zone."""
    return time.localtime(ts) if utc_offset_sec is None else time.gmtime(ts + utc_offset_sec)


def local_day(ts=None, *, utc_offset_sec: float | None = None) -> str:
    """'2026-10-18' for a timestamp (local time; see _local for the offset)."""
    return time.strftime('%Y-%m-%d', _local(ts if ts is not None else time.time(), utc_offset_sec))


def format_local_ts(ts, *, utc_offset_sec: float | None = None) -> str:
    """'Oct 21, 2026 · 03:05 PM' for a timestamp (local time; see _local for the offset), '' for none."""
    if not ts:
        return ''
    try:
        return time.strftime('%b %d, %Y · %I:%M %p', _local(ts, utc_offset_sec))
    except (OverflowError, ValueError, OSError):
        return ''


def clock_text(ts, *, utc_offset_sec: float | None = None) -> str:
    """'3:05 PM' style time (local time; see _local for the offset)."""
    return time.strftime('%I:%M %p', _local(ts, utc_offset_sec)).lstrip('0')


def parse_hhmm(text: str) -> tuple[int, int]:
    """'7:30' or '07:30' → (7, 30). Raises ValueError for anything else."""
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', str(text))
    if not m:
        raise ValueError(f'"{text}" is not a time like 07:30')
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f'"{text}" is not a time like 07:30')
    return h, mi


def at_time_today(hhmm: str, now: float, *, utc_offset_at: Callable[[float], float] | None = None) -> float:
    """Timestamp of today's hh:mm (local time) for the day that contains `now`. `utc_offset_at(ts)`: how far the
    local time is ahead of UTC at `ts` (a Clock's utc_offset_sec), or None for this computer's own time zone.

    On the day the clocks change, `now` and hh:mm can be on different offsets (00:30 still summer time, 07:00
    already winter time), so the offset is asked again at the time worked out, as time.mktime does."""
    h, m = parse_hhmm(hhmm)
    if utc_offset_at is None:
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
    lt = _local(now, utc_offset_at(now))
    as_if_utc = calendar.timegm((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, 0))
    first_guess = as_if_utc - utc_offset_at(now)
    return as_if_utc - utc_offset_at(first_guess)


def slug(text: str) -> str:
    """'pen/pencil' → 'pen-pencil' (safe for ids and URLs)."""
    s = re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')
    return s or 'object'


def title_label(label: str) -> str:
    """Detector word → a name: 'pen/pencil' → 'Pen', 'head phone' → 'Head Phone'."""
    first = str(label).split('/')[0].strip()
    return ' '.join(w.capitalize() for w in first.split()) or 'Object'


def in_hour_window(hour: int, start: int, end: int) -> bool:
    """True if `hour` is inside [start, end) on a 24-hour clock, wrapping past midnight."""
    if start == end:
        return False
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end
