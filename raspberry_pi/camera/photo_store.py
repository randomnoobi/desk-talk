"""
The pictures the Pi keeps on its own disk, in one folder per day (~/desk-talk-photos/2026-10-21/).

They get deleted on a schedule (settings → pi.delete_every_min, pi.keep_last_min), and right away,
oldest first, when the disk gets nearly full (pi.min_free_mb). A picture that can't be deleted (say, a
file left by root) is skipped, and one line in the log says so.

Saving never waits for a cleanup: the lock is only held for a moment (saving one picture, removing an
empty day folder). The number of stored pictures is counted once at the start and then kept up to date,
so asking for it never lists the folder, however big it gets.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

MB = 1024 * 1024


class PhotoStore:
    def __init__(self, folder: str | Path, keep_last_sec: float, min_free_mb: float,
                 free_mb: Callable[[], float] | None = None):
        self.folder = Path(folder).expanduser()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.keep_last_sec = float(keep_last_sec)
        self.min_free_mb = float(min_free_mb)
        self._free_mb = free_mb or (lambda: shutil.disk_usage(self.folder).free / MB)
        self._lock = threading.Lock()         # held only for a moment: saving never waits for a cleanup
        self.last_cleanup: float | None = None
        self.deleted_total = 0
        self._count = len(self.photos())      # counted once; save() and the deletes keep it up to date

    def save(self, jpeg: bytes, taken_at: float, frame_id: str) -> Path:
        """Save one picture. Its file time is the time it was taken, so cleanup goes by that."""
        when = time.localtime(taken_at)
        name = time.strftime('%H-%M-%S', when) + f'.{int(taken_at * 1000) % 1000:03d}_{frame_id}.jpg'
        day = self.folder / time.strftime('%Y-%m-%d', when)
        with self._lock:
            day.mkdir(parents=True, exist_ok=True)
            path = day / name
            part = day / (name + '.part')
            part.write_bytes(jpeg)
            os.utime(part, (taken_at, taken_at))
            new = not path.exists()
            os.replace(part, path)
            self._count += new
        return path

    def photos(self) -> list[Path]:
        """Every stored picture, oldest first."""
        return sorted(self.folder.glob('*/*.jpg'))

    def count(self) -> int:
        """How many pictures are stored (kept up to date, so this never lists the folder)."""
        return self._count

    def free_mb(self) -> float:
        return self._free_mb()

    def cleanup(self, now: float) -> int:
        """Delete pictures taken more than keep_last_sec before `now` (all of them when it's 0)."""
        cutoff = now - self.keep_last_sec
        removed = 0
        problems: list[OSError] = []
        for path in self.photos():
            try:
                if path.stat().st_mtime <= cutoff:
                    removed += self._delete(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                problems.append(exc)
        for day in self.folder.iterdir():
            with self._lock:                  # so save() never finds its day folder gone halfway through
                try:
                    if day.is_dir() and not any(day.iterdir()):
                        day.rmdir()
                except OSError as exc:
                    problems.append(exc)
        self.last_cleanup = now
        self._say_problems(problems)
        self.deleted_total += removed
        return removed

    def free_up_space(self) -> int:
        """Disk nearly full: delete the oldest pictures until there is room again."""
        removed = 0
        problems: list[OSError] = []
        for path in self.photos():
            if self.free_mb() >= self.min_free_mb:
                break
            try:
                removed += self._delete(path)
            except OSError as exc:
                problems.append(exc)
        self._say_problems(problems)
        self.deleted_total += removed
        return removed

    def _delete(self, path: Path) -> int:
        """Delete one picture and take it off the count. Returns 1 if it was deleted here, 0 if it was already gone."""
        try:
            path.unlink()
            gone = 1
        except FileNotFoundError:
            gone = 0                          # someone else deleted it: it's not stored any more either way
        with self._lock:
            self._count = max(0, self._count - 1)
        return gone

    def _say_problems(self, problems: list[OSError]) -> None:
        """One log line for everything a cleanup couldn't delete."""
        if problems:
            log.warning('Could not delete %d file(s) in %s, for example: %s. Check who owns them (ls -l).',
                        len(problems), self.folder, problems[0])
