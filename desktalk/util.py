"""Small helpers used all over the project."""

from __future__ import annotations

import re
import time


def a_or_an(word) -> str:
    return 'an' if str(word)[:1].lower() in 'aeiou' else 'a'


def format_duration(seconds) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f'{int(seconds)}s'
    if seconds < 3600:
        return f'{int(seconds / 60)}m {int(seconds % 60)}s'
    return f'{int(seconds / 3600)}h {int((seconds % 3600) / 60)}m'


def local_day(ts=None) -> str:
    """'2026-10-18' for a timestamp (local time)."""
    return time.strftime('%Y-%m-%d', time.localtime(ts if ts is not None else time.time()))


def format_local_ts(ts) -> str:
    if not ts:
        return ''
    try:
        return time.strftime('%b %d, %Y · %I:%M %p', time.localtime(ts))
    except (OverflowError, ValueError, OSError):
        return ''


def clock_text(ts) -> str:
    """'3:05 PM' style time."""
    return time.strftime('%I:%M %p', time.localtime(ts)).lstrip('0')


def parse_hhmm(text: str) -> tuple[int, int]:
    """'7:30' or '07:30' → (7, 30). Raises ValueError for anything else."""
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2})\s*', str(text))
    if not m:
        raise ValueError(f'"{text}" is not a time like 07:30')
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f'"{text}" is not a time like 07:30')
    return h, mi


def at_time_today(hhmm: str, now: float) -> float:
    """Timestamp of today's hh:mm (local time) for the day that contains `now`."""
    h, m = parse_hhmm(hhmm)
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))


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
