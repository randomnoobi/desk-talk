"""
Where log messages go: the terminal, and a file that rolls over when it gets big.

    log_file = setup_logging('data/logs', 'INFO', max_mb=5, keep_files=3)

Each part logs with its own logger (log = logging.getLogger(__name__)), so every line says where it came from:

    2026-10-21 14:03:22 WARNING desktalk.chat.lens: the picture is blurry
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = 'desktalk.log'
LINE_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'     # time, level, part name, message
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
HANDLER_NAME = 'desktalk'          # marks the handlers setup_logging added


def setup_logging(folder: str | Path, level: str | int, max_mb: float, keep_files: int) -> Path:
    """Send log messages at `level` and above ('INFO', 'warning', logging.DEBUG ...) to the terminal and to
    desktalk.log in `folder` (made if missing). When the file reaches `max_mb`, it becomes desktalk.log.1
    and so on; only `keep_files` old files are kept (0 for either means the file never rolls over).
    Calling it again replaces the handlers from the last call instead of adding more.
    Returns the log file's path."""
    root = logging.getLogger()
    root.setLevel(level.upper() if isinstance(level, str) else level)     # first, so a bad name changes nothing
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_FILE_NAME
    terminal = logging.StreamHandler()
    file = RotatingFileHandler(path, maxBytes=int(max_mb * 1024 * 1024), backupCount=keep_files, encoding='utf-8')
    # Swap out the handlers an earlier call added, so each message is still written once.
    for old in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
        root.removeHandler(old)
        old.close()
    for handler in (terminal, file):
        handler.set_name(HANDLER_NAME)
        handler.setFormatter(logging.Formatter(LINE_FORMAT, TIME_FORMAT))
        root.addHandler(handler)
    return path
