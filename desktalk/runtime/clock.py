"""The real clock: the computer's own time. It's the same one the Pi uses (shared/desktalk_shared/clock.py)."""

from __future__ import annotations

from pathlib import Path

from desktalk_shared.clock import SystemClock

__all__ = ['SystemClock', 'clock_synced']


def clock_synced(flag_file: Path | None) -> bool:
    """Has this computer's clock been set from the internet since it started? `flag_file` is a file that is there
    only once it has (settings → object_states.clock_synced_file); None means the clock is always trusted."""
    return flag_file is None or flag_file.exists()
