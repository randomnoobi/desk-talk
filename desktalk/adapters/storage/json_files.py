"""
Small JSON documents, one file per key, under one folder (data/ in the app):

    preferences          → data/preferences.json
    habits/2026-10-21    → data/habits/2026-10-21.json

These are the same files the app has always written, so data saved before keeps loading.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SUFFIX = '.json'
# A key is one or more plain names joined by "/": letters, digits, "-", "_" and "." (not starting with ".").
_KEY = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*')


class JsonFileStore:
    """The KeyValueStore (desktalk/ports.py). Saves are all-or-nothing: the new file is written next to the
    old one and then swapped in, so a crash or a full disk never leaves a half-written file."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError(f'{key!r} is not a usable key: use names like "preferences" or "habits/2026-10-21"')
        return self.root / f'{key}{SUFFIX}'

    def load(self, key: str, default: Any = None) -> Any:
        """The saved value (a fresh copy each time), or `default` if there is none or the file is broken."""
        path = self._path(key)
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as exc:
            log.warning('Could not read %s, so it counts as empty: %s', path, exc)
            return default

    def save(self, key: str, value: Any) -> None:
        """Write `value` as JSON. Raises TypeError if it can't be written as JSON, OSError if the disk fails;
        either way the file keeps its old value."""
        path = self._path(key)
        text = json.dumps(value, indent=2)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def keys(self, prefix: str = '') -> list[str]:
        """Saved keys starting with `prefix`, sorted."""
        if not self.root.is_dir():
            return []
        found = (p.relative_to(self.root).as_posix()[:-len(SUFFIX)] for p in self.root.rglob(f'*{SUFFIX}'))
        return sorted(k for k in found if k.startswith(prefix) and _KEY.fullmatch(k))
