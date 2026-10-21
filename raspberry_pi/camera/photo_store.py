"""
The pictures the Pi keeps on its own disk, in one folder per day (~/desk-talk-photos/2026-10-21/).

They get deleted on a schedule (settings → pi.delete_every_min, pi.keep_last_min), and right away,
oldest first, when the disk gets nearly full (pi.min_free_mb).
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

MB = 1024 * 1024


class PhotoStore:
    def __init__(self, folder: str | Path, keep_last_sec: float, min_free_mb: float,
                 free_mb: Callable[[], float] | None = None):
        self.folder = Path(folder).expanduser()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.keep_last_sec = float(keep_last_sec)
        self.min_free_mb = float(min_free_mb)
        self._free_mb = free_mb or (lambda: shutil.disk_usage(self.folder).free / MB)
        self._lock = threading.Lock()
        self.last_cleanup: float | None = None
        self.deleted_total = 0

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
            os.replace(part, path)
        return path

    def photos(self) -> list[Path]:
        """Every stored picture, oldest first."""
        return sorted(self.folder.glob('*/*.jpg'))

    def count(self) -> int:
        return len(self.photos())

    def free_mb(self) -> float:
        return self._free_mb()

    def cleanup(self, now: float) -> int:
        """Delete pictures taken more than keep_last_sec before `now` (all of them when it's 0)."""
        cutoff = now - self.keep_last_sec
        removed = 0
        with self._lock:
            for path in self.photos():
                try:
                    if path.stat().st_mtime <= cutoff:
                        path.unlink()
                        removed += 1
                except FileNotFoundError:
                    pass
            for day in self.folder.iterdir():
                if day.is_dir() and not any(day.iterdir()):
                    day.rmdir()
            self.last_cleanup = now
        self.deleted_total += removed
        return removed

    def free_up_space(self) -> int:
        """Disk nearly full: delete the oldest pictures until there is room again."""
        removed = 0
        with self._lock:
            for path in self.photos():
                if self.free_mb() >= self.min_free_mb:
                    break
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
        self.deleted_total += removed
        return removed
