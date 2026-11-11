"""Free space on the disk a folder is on: the data folder, which on the Pi is on its SD card (the health checks)."""

from __future__ import annotations

import shutil
from pathlib import Path

MEGABYTE = 1024 * 1024


class FolderDiskSpace:
    """A DiskSpace (desktalk/ports.py) for `folder`."""

    def __init__(self, folder: Path):
        self._folder = folder

    def free_mb(self) -> float | None:
        try:
            return shutil.disk_usage(self._folder).free / MEGABYTE
        except OSError:
            return None
