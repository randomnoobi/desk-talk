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

log = logging.getLogger(__name__)


def saving_from_environment(environ: Mapping[str, str]) -> tuple[Saving, Path | None]:
    """The saving mode and, with `off`, the in-memory folder. Nothing set, or a mode it doesn't know, is plain (a
    mode it doesn't know is logged). `off` with no folder is logged too, and gives None: build() then makes a new
    temporary folder."""
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
        log.warning('%s is off but %s is empty, so a new temporary folder is used', SAVING_NAME, FOLDER_NAME)
        return Saving.OFF, None
    return Saving.OFF, Path(folder)


def folder_for(saving: Saving, folder: Path | None) -> Path | None:
    """The folder this run writes to: `folder`, or with `off` and no folder a new temporary one, so nothing is
    written to the project's data folder."""
    if saving is Saving.OFF and folder is None:
        return Path(tempfile.mkdtemp())
    return folder
