"""The real clock: the computer's own time. It's the same one the Pi uses (shared/desktalk_shared/clock.py)."""

from __future__ import annotations

from pathlib import Path

from desktalk_shared.clock import SystemClock

__all__ = ['SystemClock', 'boot_id', 'clock_synced']


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
