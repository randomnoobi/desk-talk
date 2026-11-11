"""The real clock: the computer's own time. It's the same one the Pi uses (shared/desktalk_shared/clock.py).
Also how far to trust it: has it been set from the internet since this computer started, and is it believable at
all (ClockCheck)? docs/CLOCK.md says why a Pi's clock can be wrong."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime
from pathlib import Path

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

    believable(now): is `now` on or after settings → clock.earliest_believable_date? A clock before it is plainly
    wrong (set to 1990 by hand, say), so nothing is deleted by date until it is right; a warning says so once.
    synced(now): believable, and set from the internet since this computer started (`flag_file`; None: always).
    The flag file is looked at again on every call until it is there; after that it isn't looked at again, since it
    stays until the next boot."""

    def __init__(self, flag_file: Path | None, earliest_believable: date):
        self._flag_file = flag_file
        self._earliest = earliest_believable
        self._earliest_ts = datetime(earliest_believable.year, earliest_believable.month, earliest_believable.day,
                                     tzinfo=UTC).timestamp()
        self._lock = threading.Lock()
        self._flag_seen = flag_file is None
        self._warned = False

    def believable(self, now: float) -> bool:
        ok = now >= self._earliest_ts
        with self._lock:
            changed = ok == self._warned          # it turned wrong, or right again, since the last call
            self._warned = not ok
        if changed and not ok:
            log.warning('The clock says %s, before clock.earliest_believable_date (%s), so it must be wrong: '
                        'nothing is deleted by date, and saved object states are not picked up, until it is right',
                        _day(now), self._earliest.isoformat())
        elif changed:
            log.info('The clock looks right again (%s)', _day(now))
        return ok

    def synced(self, now: float) -> bool:
        if not self.believable(now):
            return False
        with self._lock:
            if not self._flag_seen:
                self._flag_seen = clock_synced(self._flag_file)
            return self._flag_seen


def _day(now: float) -> str:
    try:
        return datetime.fromtimestamp(now, UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return f'{now:.0f} seconds since 1970'
