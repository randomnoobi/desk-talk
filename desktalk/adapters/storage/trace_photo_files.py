"""
Each object's story pictures on disk (settings → object_story.photos_folder), one folder per kind of object:

    data/trace_photos/cup/index.json                 what each picture is: when, why, the box (oldest first)
    data/trace_photos/cup/1760000000123-appeared-a1b2c3.jpg
    data/trace_photos/pen-pencil/...                 a detector word with a slash or a space gets a plain name

Only the newest few timed pictures per kind of object are kept (object_story.timed_photos_kept) and, apart from
them, the newest few step pictures (object_story.step_photos_kept), so they can't fill the disk and a long stay
never pushes out how the object got there.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from desktalk.adapters.storage.files import write_atomically
from desktalk.domain import TracePhoto, newest_trace_photos

log = logging.getLogger(__name__)

INDEX = 'index.json'
PICTURE = '.jpg'
_PLAIN_ID = re.compile(r'[A-Za-z0-9_-]+')          # a picture's id is also its file name
_NOT_PLAIN = re.compile(r'[^a-z0-9]+')


def folder_name(label: str) -> str:
    """A detector word as a folder name: 'pen/pencil' → 'pen-pencil', 'cell phone' → 'cell-phone'."""
    return _NOT_PLAIN.sub('-', label.lower()).strip('-') or '-'


def _photo(entry: Any) -> TracePhoto | None:
    """One index entry back as a TracePhoto; None for one that doesn't read (a hand edit)."""
    try:
        x1, y1, x2, y2 = (float(v) for v in entry['box'])
        width, height = (int(v) for v in entry['size'])
        return TracePhoto(id=str(entry['id']), label=str(entry['label']), ts=float(entry['ts']),
                          why=str(entry['why']), confidence=float(entry['confidence']), box=(x1, y1, x2, y2),
                          size=(width, height))
    except (KeyError, TypeError, ValueError):
        return None


def _write_bytes(path: Path, data: bytes) -> None:
    """Write a picture all-or-nothing, like write_atomically does for text."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class TracePhotoFiles:
    """The TracePhotos (desktalk/ports.py), kept as files. add() is called from the TracePhotoTaker's own thread
    and the reads from the web server's, so a lock keeps an index from being read half-changed."""

    def __init__(self, folder: Path, timed_kept: int, steps_kept: int):
        self._folder = Path(folder)
        self._timed_kept = timed_kept
        self._steps_kept = steps_kept
        self._lock = threading.Lock()

    def add(self, photo: TracePhoto, jpeg: bytes) -> None:
        """Keep a picture, and delete the ones of that kind of object newest_trace_photos leaves out.
        Raises OSError when the disk fails (the taker logs it)."""
        if not _PLAIN_ID.fullmatch(photo.id):
            raise ValueError(f'{photo.id!r} is not a plain picture id (letters, digits, - and _)')
        where = self._folder / folder_name(photo.label)
        with self._lock:
            where.mkdir(parents=True, exist_ok=True)
            _write_bytes(where / f'{photo.id}{PICTURE}', jpeg)
            photos = newest_trace_photos([p for p in self._read(where) if p.id != photo.id] + [photo],
                                         self._timed_kept, self._steps_kept)
            write_atomically(where / INDEX, json.dumps([asdict(p) for p in photos], indent=1))
            # Every picture not in the index goes: the ones just pushed out, and any left by a broken index.
            kept = {p.id for p in photos}
            for old in where.glob(f'*{PICTURE}'):
                if old.stem not in kept:
                    old.unlink(missing_ok=True)

    def photos(self, label: str) -> list[TracePhoto]:
        with self._lock:
            return [p for p in self._read(self._folder / folder_name(label)) if p.label == label]

    def jpeg(self, photo_id: str) -> bytes | None:
        if not _PLAIN_ID.fullmatch(photo_id) or not self._folder.is_dir():
            return None
        for where in self._folder.iterdir():
            try:
                return (where / f'{photo_id}{PICTURE}').read_bytes()
            except OSError:                     # not in this folder (or not a folder)
                continue
        return None

    def _read(self, where: Path) -> list[TracePhoto]:
        """A folder's index, oldest first; [] when there is none or it's broken (logged)."""
        try:
            entries = json.loads((where / INDEX).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.warning('Could not read %s, so it counts as no pictures: %s', where / INDEX, exc)
            return []
        found = [_photo(entry) for entry in entries] if isinstance(entries, list) else []
        return sorted((p for p in found if p is not None), key=lambda p: p.ts)
