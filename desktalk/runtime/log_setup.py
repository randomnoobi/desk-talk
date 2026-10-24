"""
Where log messages go: the terminal, and a file that rolls over when it gets big.

    log_file = setup_logging('data/logs', 'INFO', max_mb=5, keep_files=3, file_name='desktalk.log')

Each part logs with its own logger (log = logging.getLogger(__name__)), so every line says where it came from:

    2026-10-21 14:03:22 WARNING desktalk.chat.lens: the picture is blurry
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LINE_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'     # time, level, part name, message
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
HANDLER_NAME = 'desktalk'          # marks the handlers setup_logging added
STARTUP_LOGGER = 'desktalk.startup'    # server.py's start-up lines: shown as they are, at any log level


class _TerminalLines(logging.Formatter):
    """The usual line, except for the start-up lines, which show as they are (the banner, the QR code)."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage() if record.name == STARTUP_LOGGER else super().format(record)


def setup_logging(folder: str | Path, level: str | int, max_mb: float, keep_files: int, file_name: str) -> Path:
    """Send log messages at `level` and above ('INFO', 'warning', logging.DEBUG ...) to the terminal and to
    the file `file_name` (logging.file_name, e.g. desktalk.log) in `folder` (made if missing). When the file
    reaches `max_mb` (1 MB = 1024 x 1024 bytes), it becomes desktalk.log.1 and so on; only `keep_files` old
    files are kept (0 for either means the file never rolls over).
    Calling it again replaces the handlers from the last call instead of adding more.
    The start-up lines (logger STARTUP_LOGGER) always show, and in the terminal without the time and level.
    Returns the log file's path."""
    root = logging.getLogger()
    root.setLevel(level.upper() if isinstance(level, str) else level)     # first, so a bad name changes nothing
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name
    logging.getLogger(STARTUP_LOGGER).setLevel(logging.INFO)     # even when `level` is WARNING or ERROR
    terminal = logging.StreamHandler()
    file = RotatingFileHandler(path, maxBytes=int(max_mb * 1024 * 1024), backupCount=keep_files, encoding='utf-8')
    # Swap out the handlers an earlier call added, so each message is still written once.
    for old in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
        root.removeHandler(old)
        old.close()
    terminal.setFormatter(_TerminalLines(LINE_FORMAT, TIME_FORMAT))
    file.setFormatter(logging.Formatter(LINE_FORMAT, TIME_FORMAT))
    for handler in (terminal, file):
        handler.set_name(HANDLER_NAME)
        root.addHandler(handler)
    return path
