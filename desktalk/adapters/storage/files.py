"""Writing a file all-or-nothing."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomically(path: Path, text: str) -> None:
    """Replace `path` with `text`. The new file is written next to the old one, then swapped in, so a crash or
    a full disk leaves either the old file or the new one, never half of it. Makes the folder if needed."""
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
