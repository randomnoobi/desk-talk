"""
Docker's health check for the `app` and `detector` containers: does the program answer?

Docker only labels a container "unhealthy" and leaves it hung, with nobody there to act. So past a number of failed
checks in a row this ends the program, and restart: unless-stopped brings the container back.

    HEALTH_URL                     what to ask
    HEALTH_ANSWER_WAIT_SEC         how long an answer may take. Must be less than the health check's timeout in Docker
    HEALTH_GRACE_SEC               failures this soon after a start don't count (loading is slow)
    HEALTH_FAILS_BEFORE_RESTART    failures in a row that end the program

The count lives in a file, as each check is a program of its own. The containers run with init: true, so PID 1 is
Docker's small init and the program is its child: a program that is PID 1 can't be stopped from inside, however hung.
"""

from __future__ import annotations

import os
import signal
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

COUNTER = Path('/tmp/desktalk-health-failures')
PROC = Path('/proc')
INIT = 1
OK, FAILED = 0, 1
# The fields of /proc/<pid>/stat after the name in brackets, counted from 0
PARENT_FIELD = 1
STARTED_FIELD = 19


@dataclass(frozen=True)
class Settings:
    url: str
    answer_wait_sec: float
    grace_sec: float
    fails_before_restart: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Settings:
        return cls(environ['HEALTH_URL'], float(environ['HEALTH_ANSWER_WAIT_SEC']), float(environ['HEALTH_GRACE_SEC']),
                   int(environ['HEALTH_FAILS_BEFORE_RESTART']))


def answers(url: str, wait_sec: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=wait_sec):     # any status from 400 up raises
            return True
    except (OSError, urllib.error.URLError):
        return False


def _stat_fields(proc: Path, pid: str | int) -> list[str]:
    return (proc / str(pid) / 'stat').read_text(encoding='latin-1').rsplit(')', 1)[1].split()


def container_age_sec(proc: Path, ticks_per_sec: int) -> float:
    up_sec = float((proc / 'uptime').read_text(encoding='ascii').split()[0])
    return up_sec - int(_stat_fields(proc, INIT)[STARTED_FIELD]) / ticks_per_sec


def main_processes(proc: Path) -> list[int]:
    """The children of PID 1. Docker starts this health check itself, so it isn't one of them."""
    found = []
    for folder in proc.iterdir():
        if folder.name.isdigit():
            try:
                if _stat_fields(proc, folder.name)[PARENT_FIELD] == str(INIT):
                    found.append(int(folder.name))
            except OSError:         # it ended just now
                continue
    return sorted(found)


def _say(line: str) -> None:
    """In the container's own log, for whoever wonders why it restarted. If that can't be opened, on the check's."""
    try:
        with (PROC / str(INIT) / 'fd' / '1').open('a', encoding='utf-8') as log:
            log.write(line + '\n')
    except OSError:
        sys.stderr.write(line + '\n')


def _count(counter: Path) -> int:
    try:
        return int(counter.read_text(encoding='ascii'))
    except (OSError, ValueError):
        return 0


def check(settings: Settings, *, proc: Path = PROC, counter: Path = COUNTER,
          ask: Callable[[str, float], bool] = answers, kill: Callable[[int, int], None] = os.kill,
          ticks_per_sec: int | None = None) -> int:
    """OK or FAILED, as Docker wants them. Past the number of failures, the program has been ended."""
    if ask(settings.url, settings.answer_wait_sec):
        counter.unlink(missing_ok=True)
        return OK
    if container_age_sec(proc, ticks_per_sec or os.sysconf('SC_CLK_TCK')) < settings.grace_sec:
        counter.unlink(missing_ok=True)
        return FAILED
    failures = _count(counter) + 1
    if failures < settings.fails_before_restart:
        counter.write_text(str(failures), encoding='ascii')
        return FAILED
    counter.unlink(missing_ok=True)
    _say(f'No answer from {settings.url} {failures} times in a row, so this container is starting again.')
    programs = main_processes(proc)
    for pid in programs:
        kill(pid, signal.SIGKILL)
    if not programs:                # no init: true. This is all that can be tried on a PID 1
        kill(INIT, signal.SIGTERM)
    return FAILED


if __name__ == '__main__':
    sys.exit(check(Settings.from_env(os.environ)))
