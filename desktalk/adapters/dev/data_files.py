"""
The data folder viewer for the developer page's storage tab: what's in the data folder on this computer (the
event log days, object_states.sqlite, the JSON stores, the logs) and the start of any file in it. Read-only.

Nothing outside the data folder is ever listed or read: a path is resolved first (which follows `..` and
symlinks, and lets an absolute path replace the folder) and refused unless it still lands inside the folder.
A .sqlite file isn't read as text; the message points to the database tab instead.
"""

from __future__ import annotations

import os
from pathlib import Path

from desktalk.config.prompts import DevWords
from desktalk.domain import DataFile

DATABASE_SUFFIX = '.sqlite'     # the object state history; the database tab shows what's inside


class DataFolder:
    """DataFiles over one folder. `preview_bytes` is how much of a file preview() reads (settings →
    dev.file_preview_kb); `words` are the messages from config/prompts.yaml (prompts → dev)."""

    def __init__(self, folder: Path, preview_bytes: int, *, words: DevWords):
        self._folder = Path(folder)
        self._preview_bytes = preview_bytes
        self._words = words

    def list(self) -> list[DataFile]:
        root = self._folder.resolve()
        if not root.is_dir():
            return []
        found: list[DataFile] = []
        for here, folders, files in os.walk(root):     # doesn't go into symlinked folders
            for name in folders + files:
                entry = self._describe(root, Path(here) / name)
                if entry is not None:
                    found.append(entry)
        return sorted(found, key=lambda f: f.path)

    def preview(self, path: str) -> str:
        root = self._folder.resolve()
        resolved = _resolve(root / path)
        if resolved is not None and not resolved.is_relative_to(root):
            raise ValueError(self._words.outside_data_folder.format(path=path))
        if resolved is None or not resolved.exists():
            raise FileNotFoundError(self._words.no_such_file.format(path=path))
        if resolved.is_dir():
            raise ValueError(self._words.is_a_folder.format(path=path))
        if resolved.suffix == DATABASE_SUFFIX:
            return self._words.database_file.format(path=path)
        with resolved.open('rb') as file:
            return file.read(self._preview_bytes).decode('utf-8', errors='replace')

    @staticmethod
    def _describe(root: Path, entry: Path) -> DataFile | None:
        """One file or folder found inside, or None for a symlink pointing outside, a broken one, or one that
        went away while we looked (or symlinks in a loop)."""
        resolved = _resolve(entry)
        if resolved is None or not resolved.is_relative_to(root):
            return None
        try:
            stat = entry.stat()
        except OSError:
            return None
        is_folder = entry.is_dir()
        return DataFile(path=entry.relative_to(root).as_posix(), bytes=0 if is_folder else stat.st_size,
                        modified=stat.st_mtime, is_folder=is_folder)


def _resolve(path: Path) -> Path | None:
    """Where `path` really is, following `..` and symlinks; None for symlinks in a loop, which lead nowhere."""
    try:
        return path.resolve()
    except (RuntimeError, OSError):     # Python 3.12 raises RuntimeError for a loop, newer ones OSError
        return None
