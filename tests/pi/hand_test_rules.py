"""
Plain rules for tests/pi/test_failure_checks.py: what the checks after a hand test (docs/PI_CHECKS.md, "Failure hand
tests") make of what they read from the Pi. No SSH here, so tests/pi/test_hand_test_rules.py runs them with no Pi.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, NamedTuple

from tests.pi.on_the_pi import (
    _before_this_restore,
    clock_behind_warning_sec,
    clock_not_synced_at_this_start,
)

# `docker logs -t` puts Docker's time in front of each line, like 2026-11-11T05:26:03.260764230Z
DOCKER_TIME = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z\s')
PICTURES_TAKEN = re.compile(r'Pictures taken (\d+),')
NO_JOURNAL_ACCESS = ('insufficient permissions', 'not seeing messages')
# What the app writes when it won't believe its clock: before clock.earliest_believable_date, say. The two lines
# on_the_pi.py knows are checked too, so either way counts.
UNBELIEVABLE_CLOCK_WORDS = ('earliest_believable_date', "The clock can't be right")    # check: task D


class StatusLine(NamedTuple):
    at: float           # seconds since 1970, by the Pi's clock when Docker got the line
    taken: int          # the camera's count of pictures taken so far in this run


def parse_status_lines(docker_logs: str) -> list[StatusLine]:
    """The camera's status lines in the output of `docker logs -t`, oldest first."""
    found = []
    for line in docker_logs.splitlines():
        stamp, taken = DOCKER_TIME.match(line), PICTURES_TAKEN.search(line)
        if not stamp or not taken:
            continue
        whole = datetime.fromisoformat(stamp[1]).replace(tzinfo=UTC).timestamp()
        fraction = float(f'0.{stamp[2]}') if stamp[2] else 0.0
        found.append(StatusLine(whole + fraction, int(taken[1])))
    return found


def longest_quiet_sec(lines: Sequence[StatusLine], *, since: float, now: float) -> float:
    """The longest time between `since` and `now` with no new pictures taken. A status line counts as new pictures when
    its count differs from the line before (a smaller one is a new run of the camera), or when it is the first line
    seen. Lines from before `since` only give the count to start from."""
    previous: int | None = None
    last_new = since
    longest = 0.0
    for line in lines:
        new = previous is None or line.taken != previous
        previous = line.taken
        if line.at < since or not new:
            continue
        longest = max(longest, line.at - last_new)
        last_new = line.at
    return max(longest, now - last_new)


def days_gone(before: Iterable[str], after: Iterable[str], *, today: date, keep_days: int) -> list[str]:
    """The event-log folders noted before that are gone now, leaving out the days old enough for the usual clean-up
    (event_log.keep_days, plus one day in case the Pi's day is behind `today`)."""
    oldest_allowed = today - timedelta(days=keep_days)
    still_there = set(after)
    gone = []
    for name in before:
        if name in still_there:
            continue
        try:
            if date.fromisoformat(name) < oldest_allowed:
                continue
        except ValueError:
            pass
        gone.append(name)
    return gone


def restarted_since(before: Mapping[str, Mapping[str, Any]], now: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The containers that started again (or are gone) since the note: {service: {started_at, restart_count}}."""
    return [service for service, was in before.items()
            if service not in now or now[service]['started_at'] != was['started_at']
            or now[service]['restart_count'] != was['restart_count']]


def note_problem(note: Mapping[str, Any] | None, *, mac_now: float, max_age_sec: float) -> str | None:
    """Why the note taken before a hand test can't be used, or None if it can. Its age is by the Mac's clock, as the
    Pi's may be set back on purpose."""
    if note is None:
        return ('No note from before the hand test. Take one first: DESKTALK_PI_NOTE_BEFORE=1 uv run pytest -m pi '
                '-k note_before')
    age = mac_now - note['mac_at']
    if age > max_age_sec:
        return f'The note from before the hand test is {age / 3600:.0f} hours old. Take a new one and do it again.'
    return None


def wifi_dropped(journal: str, device: str) -> bool | None:
    """Did NetworkManager's journal (`journalctl -u NetworkManager --since ...`) see `device` go from connected to
    anything else? None if the user can't read the journal (they must be in the adm group)."""
    if any(words in journal for words in NO_JOURNAL_ACCESS):
        return None
    return f'device ({device}): state change: activated ->' in journal


def card_level(free_mb: float, *, warning_mb: float, serious_mb: float) -> str:
    """'fine', 'warning' or 'serious': which alert free space on the SD card should give."""
    if free_mb < serious_mb:
        return 'serious'
    return 'warning' if free_mb < warning_mb else 'fine'


def warned_since(events: Iterable[Mapping[str, Any]], keys: set[str], *, since: float) -> Mapping[str, Any] | None:
    """The newest warning Lens gave with one of `keys` at or after `since`, from /api/log/events?types=camera."""
    found = [e for e in events if e.get('kind') == 'problem' and e.get('key') in keys and e.get('ts', 0) >= since]
    return max(found, key=lambda e: e['ts']) if found else None


def clock_distrusted(log: str) -> bool:
    """Did the app say, at this start, that it doesn't believe its clock: behind its own saved states, not set from the
    internet, or plainly wrong? Then it must have picked up nothing from before."""
    return (clock_behind_warning_sec(log) is not None or clock_not_synced_at_this_start(log)
            or any(words in _before_this_restore(log) for words in UNBELIEVABLE_CLOCK_WORDS))


def stuck_jobs(health: Mapping[str, Any] | None, *, slack_sec: float) -> list[str]:
    """The timed jobs in /api/health whose next run is further off than their interval allows: a scheduler timed by
    the wall clock waits years after the clock goes back."""
    return [job['name'] for job in (health or {}).get('scheduler', [])
            if job.get('next_in_sec') is not None and job['next_in_sec'] > job['every_sec'] + slack_sec]
