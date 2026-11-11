"""The real clock: the computer's own time. It's the same one the Pi uses (shared/desktalk_shared/clock.py).
Also how far to trust it: has it been set from the internet since this computer started, and is it believable at
all, not too early and not jumped far ahead (ClockCheck)? docs/CLOCK.md says why a Pi's clock can be wrong."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from desktalk.ports import Clock
from desktalk_shared.clock import SystemClock

__all__ = ['ClockCheck', 'SystemClock', 'boot_id', 'clock_synced']

log = logging.getLogger(__name__)


def clock_synced(flag_file: Path | None) -> bool:
    """Has this computer's clock been set from the internet since it started? `flag_file` is a file that is there
    only once it has (settings → object_states.clock_synced_file); None means the clock is always trusted."""
    return flag_file is None or flag_file.exists()


def boot_id(boot_id_file: Path | None) -> str | None:
    """Which run of this computer this is: a new one after every boot, the same while only the app restarts. Read
    from `boot_id_file` (settings → object_states.boot_id_file; Linux makes it, and Docker shows the Pi's own).
    None when there's no such file (a Mac), it can't be read, or it is empty."""
    if boot_id_file is None:
        return None
    try:
        text = boot_id_file.read_text().strip()
    except OSError:
        return None
    return text or None


class ClockCheck:
    """What the app asks of its clock while it runs. Safe to call from any thread.

    believable(now): can the clock be right? Not when `now` is before settings → clock.earliest_believable_date
    (set to 1990 by hand, say), and not when it jumped more than `largest_jump_ahead_sec` ahead while the app ran
    and the time server didn't set it (set to next year by hand). Until it is right, nothing is deleted by date; a
    warning says so once.
    synced(now): believable, and set from the internet since this computer started (`flag_file`; None: always).

    How a jump is seen: the wall clock minus `clock`'s monotonic clock stays the same while time goes on, and
    changes when the wall clock is set. It is taken when the app starts and again each time the time server sets
    the clock (it touches `flag_file` each time: the file appears, or its time changes), so the sync that puts a
    Pi right after a power cut, days ahead, is believed. A jump back deletes less, never more, so only a jump ahead
    counts. With no flag file (a Mac) jumps aren't looked for: its clock is kept right, and its monotonic clock
    stops while it sleeps, so waking up looks like a jump. docs/CLOCK.md says more."""

    def __init__(self, flag_file: Path | None, earliest_believable: date, *, clock: Clock,
                 largest_jump_ahead_sec: float):
        self._flag_file = flag_file
        self._earliest = earliest_believable
        self._earliest_ts = datetime(earliest_believable.year, earliest_believable.month, earliest_believable.day,
                                     tzinfo=UTC).timestamp()
        self._clock = clock
        self._largest_jump = largest_jump_ahead_sec
        self._lock = threading.Lock()
        self._flag_seen = flag_file is None
        self._set_at = _changed_at(flag_file)          # when the time server last set the clock (the file's time)
        self._offset = clock.now() - clock.monotonic()  # the wall clock minus the monotonic one, as it was then
        self._doubt: str | None = None                  # what is wrong with the clock, as last said: early / ahead

    def believable(self, now: float) -> bool:
        ahead = self._jumped_ahead(now)
        doubt = 'early' if now < self._earliest_ts else 'ahead' if ahead > self._largest_jump else None
        with self._lock:
            changed = doubt != self._doubt              # it turned wrong, or right again, since the last call
            self._doubt = doubt
        if changed and doubt == 'early':
            log.warning('The clock says %s, before clock.earliest_believable_date (%s), so it must be wrong: '
                        'nothing is deleted by date, and saved object states are not picked up, until it is right',
                        _day(now), self._earliest.isoformat())
        elif changed and doubt == 'ahead':
            log.warning('The clock jumped %.1f hours ahead (to %s) while the app ran, more than '
                        'clock.largest_jump_ahead_min, and the time server did not set it, so it may be wrong: '
                        'nothing is deleted by date until the time server sets it again, or the app restarts',
                        ahead / 3600, _day(now))
        elif changed:
            log.info('The clock looks right again (%s)', _day(now))
        return doubt is None

    def synced(self, now: float) -> bool:
        if not self.believable(now):
            return False
        with self._lock:
            if not self._flag_seen:
                self._flag_seen = clock_synced(self._flag_file)
            return self._flag_seen

    def _jumped_ahead(self, now: float) -> float:
        """How many seconds the wall clock moved ahead of the monotonic one since the app started or the time server
        last set it (0 or less: it didn't)."""
        if self._flag_file is None:
            return 0.0
        offset = now - self._clock.monotonic()
        set_at = _changed_at(self._flag_file)
        with self._lock:
            if set_at is not None and set_at != self._set_at:      # the time server set the clock (again)
                self._set_at, self._offset = set_at, offset
            return offset - self._offset


def _changed_at(flag_file: Path | None) -> int | None:
    """When the flag file was last touched (nanoseconds), or None when there's no such file."""
    if flag_file is None:
        return None
    try:
        return flag_file.stat().st_mtime_ns
    except OSError:
        return None


def _day(now: float) -> str:
    try:
        return datetime.fromtimestamp(now, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return f'{now:.0f} seconds since 1970'
