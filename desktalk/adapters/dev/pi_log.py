"""
The developer page's Pi log tab (ports.PiLogReader): the Pi's log, read over ssh (settings → dev.pi_log).

- read() starts `ssh ... 'echo MARKER; <the source's start command>'` the first time a source is asked for, as a
  child process in its own process group. ssh gets a terminal on the Pi (-tt in settings → ssh), so the command
  there ends when the connection does. Another source being asked for ends the one before and lets its lines go;
  the line numbers keep counting up all the same.
- Two plain threads read ssh's printout: each line it prints becomes a 'line' (cut to settings → line_max_chars),
  each error line ssh itself prints a 'problem' (through the terminal the Pi's own errors come with its lines).
  The marker line itself isn't kept: it means ssh got through to the Pi. Line ends (\\r\\n through the terminal)
  are taken off.
- When ssh ends by itself, the reader waits (settings → retry_start_sec, twice as long after each miss up to
  retry_max_sec) and tick() connects again with the source's resume command, from the Pi's own time on the newest
  line it got: {since} is that time as seconds since 1970 with its fraction (1762855200.123456789). The lines from
  exactly that time come again, so a line with the same time and text as one of the newest kept lines is left out.
  The wait only starts small again after a connection that lasted settings → connected_ok_sec, so a command that
  fails right after connecting (a missing folder, Docker stopped) doesn't try again every retry_start_sec forever.
- tick() also stops reading once the tab hasn't asked for settings → stop_after_idle_sec. Asking for the same
  source again (also after close()) picks up where it stopped, the same way: the kept lines stay, and the resume
  command goes on from the newest time (the start command only when no time was ever seen).

Only the newest settings → keep_lines lines are kept. No shell is used on this side: the commands only come from
the settings, and the one thing filled in from outside them (the Pi's time) is quoted first. There is one reader
for the whole app, so two pages showing different logs at once keep restarting each other's ssh.
"""

from __future__ import annotations

import calendar
import itertools
import logging
import re
import shlex
import subprocess
import threading
from collections import OrderedDict, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO

from desktalk.adapters.dev.processes import end_process, text_of
from desktalk.config.app import DevPiLogSettings, DevPiLogSource
from desktalk.config.prompts import PiLogWords
from desktalk.domain import PiLogLine, PiLogPage, PiLogSource
from desktalk.ports import Clock
from desktalk_shared.backoff import Backoff

log = logging.getLogger(__name__)

MARKER = 'desktalk-pi-log-got-through-7f3a9c'   # printed on the Pi before the command: ssh got through
NANOSECONDS = 9                                  # digits of a second the Pi's time can have
# The Pi's own time in a line: docker's --timestamps (2026-11-11T10:00:00.123456789Z), journalctl's
# short-iso-precise (2026-11-11T10:00:00.123456+0000)
ISO_TIME = re.compile(r'(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:[.,](\d+))?(Z|[+-]\d\d:?\d\d)?')

PiTime = tuple[int, int]                         # (seconds since 1970, nanoseconds)


def _time_of(found: re.Match[str]) -> PiTime | None:
    """The time ISO_TIME found, like 2026-11-11T10:00:00.123456789Z or ...+02:00 / ...+0200 (no zone: UTC), as a
    pair that compares like the real times do. None when it isn't a real time."""
    year, month, day, hour, minute, second = (int(part) for part in found.groups()[:6])
    fraction, zone = found.group(7) or '', found.group(8) or 'Z'
    try:
        datetime(year, month, day, hour, minute, second, tzinfo=UTC)    # only to check it is a real time
    except ValueError:
        return None
    seconds = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
    if zone != 'Z':
        digits = zone[1:].replace(':', '')
        offset = int(digits[:2]) * 3600 + int(digits[2:]) * 60
        seconds -= offset if zone[0] == '+' else -offset
    return seconds, int(fraction[:NANOSECONDS].ljust(NANOSECONDS, '0'))


def time_in(line: str) -> tuple[PiTime, str] | None:
    """The first ISO time in a line of the Pi's log, and its text as printed. None when there is none."""
    found = ISO_TIME.search(line)
    if found is None:
        return None
    when = _time_of(found)
    return (when, found.group()) if when is not None else None


def unix_seconds(when: PiTime) -> str:
    """A Pi time as seconds since 1970 with all its fraction (1762855200.123456789), for {since}: no float, so
    nothing is rounded."""
    seconds, nanoseconds = when
    return f'{seconds}.{nanoseconds:0{NANOSECONDS}d}'


def _seconds(value: float) -> str:
    return f'{value:g}'


@dataclass
class _Connection:
    """One ssh run. Its threads only touch the reader while it is still the newest one (same `number`)."""

    number: int
    process: subprocess.Popen[bytes]
    last_error: str = ''                                 # the last line it printed on stderr
    connected_at: float | None = None                    # clock.monotonic() when the marker came
    errors_read: threading.Event = field(default_factory=threading.Event)


class SshPiLog:
    """Follows one of settings → dev.pi_log.sources on the Pi with ssh. `stop_wait_sec` is how long ssh gets to
    end on its own before it is killed (settings → supervisor.stop_wait_sec)."""

    name = 'pi log'

    def __init__(self, settings: DevPiLogSettings, words: PiLogWords, clock: Clock, *, stop_wait_sec: float):
        self._settings = settings
        self._words = words
        self._clock = clock
        self._stop_wait_sec = stop_wait_sec
        self._sources = {source.name: source for source in settings.sources}
        self._lock = threading.Lock()
        self._source: DevPiLogSource | None = None      # the one being read (or last read)
        self._state = 'stopped'
        self._problem = ''
        self._retry_at: float | None = None
        self._backoff = Backoff(settings.retry_start_sec, settings.retry_max_sec)   # the waits before trying again
        self._last_asked = 0.0                          # clock.monotonic() of the last read()
        self._lines: deque[PiLogLine] = deque()
        self._number = 0                                # the last line number handed out
        self._dropped_up_to = 0                         # the newest number let go because too many came
        self._recent: OrderedDict[tuple[PiTime, str], None] = OrderedDict()   # (time, text) of the newest Pi lines
        self._newest: tuple[PiTime, str] | None = None  # the newest Pi time seen, and its text as printed
        self._connections = 0                           # how many ssh runs have started (each one's number)
        self._connection: _Connection | None = None

    # ── ports.PiLog ───────────────────────────────────────────────
    def sources(self) -> list[PiLogSource]:
        return [PiLogSource(source.name, source.label) for source in self._settings.sources]

    def read(self, source: str, after: int) -> PiLogPage:
        wanted = self._sources.get(source)
        if wanted is None:
            raise KeyError(self._words.unknown_source.format(name=source))
        old = None
        with self._lock:
            self._last_asked = self._clock.monotonic()
            if self._source is not wanted:
                old = self._let_go()
                self._start_fresh(wanted)
            elif self._state == 'stopped':              # back after the idle stop or close(): pick up again
                self._backoff.reset()
                self._connect(resume=True)
            page = self._page(after)
        if old is not None:
            end_process(old.process, self._stop_wait_sec)
        return page

    def close(self) -> None:
        with self._lock:
            old = self._let_go()
            self._state = 'stopped'
            self._retry_at = None
        if old is not None:
            end_process(old.process, self._stop_wait_sec)

    # ── ports.PeriodicWorker ──────────────────────────────────────
    def tick(self, now: float) -> float:
        old = None
        with self._lock:
            idle = self._clock.monotonic() - self._last_asked
            if self._state != 'stopped' and idle > self._settings.stop_after_idle_sec:
                old = self._let_go()
                self._state = 'stopped'
                self._retry_at = None
                seconds = _seconds(self._settings.stop_after_idle_sec)
                self._add(self._words.stopped.format(seconds=seconds), 'note')
                log.info("Pi log: stopped reading the Pi's log, the tab hadn't asked for %s s", seconds)
            elif self._state == 'waiting' and self._retry_at is not None and now >= self._retry_at:
                self._connect(resume=True)
        if old is not None:
            end_process(old.process, self._stop_wait_sec)
        return self._settings.check_every_sec

    # ── connecting (lock held) ────────────────────────────────────
    def _start_fresh(self, source: DevPiLogSource) -> None:
        """Read `source` from its start, letting go of everything kept from before."""
        self._source = source
        self._lines.clear()
        self._dropped_up_to = 0
        self._recent.clear()
        self._newest = None
        self._problem = ''
        self._backoff.reset()
        self._connect(resume=False)

    def _connect(self, *, resume: bool) -> None:
        source = self._source
        assert source is not None
        if resume and self._newest is not None:
            when, printed = self._newest
            command = source.resume.replace('{since}', shlex.quote(unix_seconds(when)))
            shown = shlex.join([*self._settings.ssh, command])
            self._add(self._words.reconnecting.format(since=printed, command=shown), 'note')
        else:
            command = source.start.replace('{first_lines}', str(self._settings.first_lines))
            shown = shlex.join([*self._settings.ssh, command])
            self._add(self._words.connecting.format(command=shown), 'note')
        log.info('Pi log: connecting to the Pi: %s', shown)
        self._state = 'connecting'
        self._retry_at = None
        self._connections += 1
        try:
            process = subprocess.Popen([*self._settings.ssh, f'echo {MARKER}; {command}'],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
        except OSError as error:
            self._add(self._words.cannot_start.format(reason=error), 'note')
            log.warning("Pi log: couldn't start ssh: %s", error)
            self._lost(str(error), None)
            return
        connection = _Connection(self._connections, process)
        self._connection = connection
        threading.Thread(target=self._read_errors, args=(connection,), name=f'pi-log-{connection.number}-errors',
                         daemon=True).start()
        threading.Thread(target=self._read_lines, args=(connection,), name=f'pi-log-{connection.number}',
                         daemon=True).start()

    def _lost(self, reason: str, connected_at: float | None) -> None:
        """The connection ended by itself: wait, then try again (tick()). Only one that had been connected for
        settings → connected_ok_sec makes the wait start small again."""
        self._connection = None
        self._state = 'waiting'
        self._problem = reason
        if connected_at is not None and \
                self._clock.monotonic() - connected_at >= self._settings.connected_ok_sec:
            self._backoff.reset()
        wait = self._backoff.next()
        self._retry_at = self._clock.now() + wait
        self._add(self._words.lost.format(reason=reason, seconds=_seconds(wait)), 'note')
        log.warning('Pi log: the connection to the Pi ended (%s), trying again in %s s', reason, _seconds(wait))

    def _let_go(self) -> _Connection | None:
        """Stop listening to the current ssh run; the caller ends its process (outside the lock)."""
        old, self._connection = self._connection, None
        return old

    # ── the threads ───────────────────────────────────────────────
    def _read_lines(self, connection: _Connection) -> None:
        """A connection's main thread: its printout, line by line, until ssh ends."""
        process = connection.process
        assert process.stdout is not None
        for line in self._lines_of(process.stdout):
            with self._lock:
                if self._connection is not connection:
                    break
                self._take_line(connection, line)
        process.stdout.close()
        exit_code = process.wait()
        connection.errors_read.wait(self._stop_wait_sec)    # so the last error line is there to say why
        with self._lock:
            if self._connection is connection:
                self._lost(connection.last_error or self._words.exit_code.format(code=exit_code),
                           connection.connected_at)

    def _read_errors(self, connection: _Connection) -> None:
        stderr = connection.process.stderr
        assert stderr is not None
        try:
            for line in self._lines_of(stderr):
                with self._lock:
                    if self._connection is not connection:
                        break
                    text = line[:self._settings.line_max_chars]
                    connection.last_error = text
                    self._problem = text
                    self._add(text, 'problem')
            stderr.close()
        finally:
            connection.errors_read.set()

    @staticmethod
    def _lines_of(pipe: IO[bytes]) -> Iterator[str]:
        """The lines coming out of a pipe, without their line ends (\\n or \\r\\n), as soon as each is whole (the
        last one even without its newline)."""
        waiting = ''
        for text in text_of(pipe):
            *whole, waiting = (waiting + text).split('\n')
            for line in whole:
                yield line.rstrip('\r')
        if waiting:
            yield waiting.rstrip('\r')

    def _take_line(self, connection: _Connection, line: str) -> None:
        """One line the Pi printed. (Lock held.)"""
        if line == MARKER and self._state == 'connecting':
            self._state = 'connected'
            self._problem = ''
            connection.connected_at = self._clock.monotonic()
            self._add(self._words.connected, 'note')
            log.info('Pi log: connected to the Pi')
            return
        text = line[:self._settings.line_max_chars]
        found = time_in(line)
        if found is not None:
            when = found[0]
            key = (when, text)
            if key in self._recent:             # --since gives the lines from that time again
                return
            self._recent[key] = None
            if len(self._recent) > self._settings.keep_lines:
                self._recent.popitem(last=False)
            if self._newest is None or when > self._newest[0]:
                self._newest = found
        self._add(text, 'line')

    # ── the kept lines (lock held) ────────────────────────────────
    def _add(self, text: str, kind: str) -> None:
        self._number += 1
        self._lines.append(PiLogLine(self._number, text, kind, self._clock.now()))
        if len(self._lines) > self._settings.keep_lines:
            self._dropped_up_to = self._lines.popleft().number

    def _page(self, after: int) -> PiLogPage:
        lines = self._lines
        first = lines[0].number if lines else self._number + 1      # the kept numbers follow on one by one
        skip = min(max(after - first + 1, 0), len(lines))
        newer = tuple(itertools.islice(lines, skip, None))
        name = self._source.name if self._source is not None else ''
        return PiLogPage(name, self._state, self._problem, self._retry_at if self._state == 'waiting' else None,
                         newer, self._number, after < self._dropped_up_to)
