"""The saving mode raspberry_pi/app/start.sh gives the app when its container starts (docs/superpowers/specs/
2026-11-11-reserved-data-space-design.md, requirement 5):

    DESKTALK_SAVING       plain: the data folder as always (no value, on a Mac, means this too)
                          room:  the data folder is Desk Talk's own fixed space on the SD card
                          off:   that space is missing, so nothing is saved to the card...
    DESKTALK_DATA_FOLDER  ...and what the app must keep goes in this folder in memory instead

    saving, folder = saving_from_environment(os.environ)       # server.py, once at the start
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Mapping
from pathlib import Path

from desktalk.domain import Saving

SAVING_NAME = 'DESKTALK_SAVING'
FOLDER_NAME = 'DESKTALK_DATA_FOLDER'
FOLDER_PREFIX = 'desktalk-'                 # a folder folder_for makes is named this, then letters by chance

log = logging.getLogger(__name__)


def saving_from_environment(environ: Mapping[str, str]) -> tuple[Saving, Path | None]:
    """The saving mode and, with `off`, the in-memory folder. Nothing set, or a mode it doesn't know, is plain (a
    mode it doesn't know is logged). `off` with no folder is logged too, and gives None: folder_for() then makes
    one."""
    value = environ.get(SAVING_NAME, '').strip()
    if not value:
        return Saving.PLAIN, None
    try:
        saving = Saving(value.lower())
    except ValueError:
        log.warning('%s is %r, which isn\'t one of %s, so the data folder is used as always', SAVING_NAME, value,
                    ', '.join(mode.value for mode in Saving))
        return Saving.PLAIN, None
    if saving is not Saving.OFF:
        return saving, None
    folder = environ.get(FOLDER_NAME, '').strip()
    if not folder:
        log.warning('%s is off but %s is empty, so a new folder in memory is used', SAVING_NAME, FOLDER_NAME)
        return Saving.OFF, None
    return Saving.OFF, Path(folder)


def folder_for(saving: Saving, folder: Path | None, memory_fallback: Path) -> Path | None:
    """The folder this run writes to, worked out once (server.py) for the log file and build(): `folder`, or with
    `off` and no folder a new one in `memory_fallback` (settings → storage.memory_fallback_folder, a folder in
    memory), so nothing is written to the project's data folder or the card. If none can be made there, a new one in
    the computer's own temporary folder, and the log says where it is."""
    if saving is not Saving.OFF or folder is not None:
        return folder
    try:
        return Path(tempfile.mkdtemp(prefix=FOLDER_PREFIX, dir=memory_fallback))
    except OSError as exc:
        made = Path(tempfile.mkdtemp(prefix=FOLDER_PREFIX))
        log.warning('No folder could be made in memory in %s (%s), so %s is used: it may be on the disk',
                    memory_fallback, exc, made)
        return made
