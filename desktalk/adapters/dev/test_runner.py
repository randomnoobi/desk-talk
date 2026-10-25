"""
The developer page's test runner (ports.TestRunner): runs the checks ("Run everything", settings → dev.checks)
or some tests (settings → dev.test_command plus their ids) in a child process, one run at a time.

- collect() asks pytest for the list of tests (`--collect-only`), for settings → dev.collect_markers and for the
  slow ones (to tag them 'slow') at the same time. Either taking longer than settings → dev.collect_timeout_sec,
  or a command that can't start, is a RuntimeError with the words from prompts.yaml → dev.cannot_list_tests.
- A run's steps go one after the other in one plain thread, which also reads the printout into a buffer of
  settings → dev.output_keep_kb (the oldest text goes first). A failed step doesn't stop the next one.
- Every pytest gets the plugin in pytest_report.py, which adds one JSON line per finished test to a temp file;
  status() reads the new lines, so the page sees each test as soon as it finishes.
- A run that has gone longer than settings → dev.run_timeout_min is stopped ('timed out'): a second thread looks
  at the clock every settings → dev.poll_sec while the run goes, so this happens even when the page is closed
  and nobody asks for its status. Stopping ends the whole process group: first politely, then for good after
  `stop_wait_sec`.

Only the newest run is kept: once another one starts, the one before is unknown (status() raises KeyError).
No shell is ever used: the commands only come from the settings, and a test id only from collect().
"""

from __future__ import annotations

import codecs
import contextlib
import io
import json
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from desktalk.adapters.dev import pytest_report
from desktalk.config.app import DevSettings
from desktalk.config.prompts import DevWords
from desktalk.domain import RunAlreadyGoing, RunStatus, StepStatus, TestItem, TestOutcome
from desktalk.ports import Clock

log = logging.getLogger(__name__)

CHECK = 'check'                         # RunStatus.what for "Run everything"
SLOW = 'slow'                           # the pytest mark the list tags (pyproject.toml → markers)
LIST_ONE_PER_LINE = '--verbosity=-1'    # with --collect-only, pytest prints one test id per line (even after -q)
LISTED_FINE = (0, 5)                    # pytest's exit codes for "listed" and "no test has those marks"
STILL_GOING = ('waiting', 'running')
ENCODING = 'utf-8'


class Printout:
    """A run's printout: only the newest `keep` characters are kept, as the pieces they came in, so adding a
    piece never copies the rest. Offsets count from the start of the run, also after old text has been let go."""

    def __init__(self, keep: int):
        self._keep = keep
        self._pieces: deque[str] = deque()
        self._kept = 0                  # characters in _pieces
        self._dropped = 0               # characters let go from the start

    @property
    def end(self) -> int:
        """The offset just after the last character added."""
        return self._dropped + self._kept

    def add(self, text: str) -> None:
        if not text:
            return
        self._pieces.append(text)
        self._kept += len(text)
        extra = self._kept - self._keep
        while extra > 0:
            first = self._pieces[0]
            if len(first) <= extra:
                self._pieces.popleft()
                cut = len(first)
            else:
                self._pieces[0] = first[extra:]
                cut = extra
            self._kept -= cut
            self._dropped += cut
            extra -= cut

    def after(self, offset: int) -> str:
        """The kept text from `offset` on (all of it when `offset` is before what is kept)."""
        wanted = self.end - max(offset, self._dropped)
        if wanted <= 0:
            return ''
        found: list[str] = []
        have = 0
        for piece in reversed(self._pieces):        # only as far back as the offset
            found.append(piece)
            have += len(piece)
            if have >= wanted:
                break
        text = ''.join(reversed(found))
        return text[len(text) - wanted:]


@dataclass
class _Step:
    name: str
    command: tuple[str, ...]
    state: str = 'waiting'
    exit_code: int | None = None


@dataclass
class _Run:
    run_id: str
    what: str
    started: float                      # seconds since 1970, for the page
    started_mono: float                 # clock.monotonic(), for the time limit
    steps: list[_Step]
    shows_steps: bool                   # the check shows its steps; a run of some tests has just the one
    report: Path                        # the plugin's file (pytest_report.py)
    output: Printout                    # what the commands printed (the newest settings → dev.output_keep_kb)
    state: str = 'running'
    finished: float | None = None
    outcomes: dict[str, TestOutcome] = field(default_factory=dict)     # test id → how it went, in finishing order
    report_read: int = 0                # how many bytes of the report file have been read
    process: subprocess.Popen[bytes] | None = None
    over: threading.Event = field(default_factory=threading.Event)    # set when the run's thread is done


class PytestRunner:
    """Runs the checks and the tests in `project` (the repo folder). `stop_wait_sec` is how long a stopped run
    gets to end on its own before it is killed (settings → supervisor.stop_wait_sec)."""

    def __init__(self, settings: DevSettings, words: DevWords, project: Path, clock: Clock, *,
                 stop_wait_sec: float):
        self._settings = settings
        self._words = words
        self._project = project
        self._clock = clock
        self._stop_wait_sec = stop_wait_sec
        self._lock = threading.Lock()
        self._known: frozenset[str] = frozenset()       # the test ids (and their files) the last collect() gave
        self._run: _Run | None = None
        self._runs_started = 0

    # ── ports.TestRunner ──────────────────────────────────────────
    def collect(self) -> list[TestItem]:
        listed, slow_list = self._list_tests(self._settings.collect_markers, SLOW)
        slow = set(slow_list)
        items = [TestItem(test_id, test_id.split('::', 1)[0], (SLOW,) if test_id in slow else ())
                 for test_id in listed]
        with self._lock:
            self._known = frozenset(item.id for item in items) | frozenset(item.file for item in items)
        return items

    def start_check(self) -> str:
        steps = [_Step(check.name, self._with_plugin(check.command)) for check in self._settings.checks]
        return self._start(CHECK, steps, shows_steps=True)

    def start_tests(self, ids: list[str]) -> str:
        with self._lock:
            unknown = [test_id for test_id in ids if test_id not in self._known]
        if unknown or not ids:
            raise ValueError(self._words.unknown_test.format(test_id=', '.join(unknown) or '[]'))
        command = (*self._settings.test_command, '-p', pytest_report.__name__, *ids)
        what = ' '.join(ids)
        return self._start(what, [_Step(what, command)], shows_steps=False)

    def status(self, run_id: str, output_from: int) -> RunStatus:
        run = self._find(run_id)
        self._stop_if_too_long(run)
        with self._lock:
            self._read_report(run)
            steps = tuple(StepStatus(s.name, s.state, s.exit_code) for s in run.steps) if run.shows_steps else ()
            return RunStatus(run.run_id, run.what, run.state, run.started, run.finished, steps,
                             tuple(run.outcomes.values()), run.output.after(output_from), run.output.end)

    def stop(self, run_id: str) -> None:
        with self._lock:
            run = self._run if self._run is not None and self._run.run_id == run_id else None
        if run is not None:
            self._end(run, 'stopped')

    def close(self) -> None:
        """Stop the run still going, if any (the server is stopping). A run has its own process group, so
        Ctrl+C on the server never reaches it: without this it would keep going after the server has gone."""
        with self._lock:
            run = self._run
        if run is not None:
            self._end(run, 'stopped')

    # ── starting a run ────────────────────────────────────────────
    def _start(self, what: str, steps: list[_Step], *, shows_steps: bool) -> str:
        if self._run is not None:
            self._stop_if_too_long(self._run)
        with self._lock:
            if self._run is not None and self._run.state == 'running':
                raise RunAlreadyGoing(self._words.run_already_going)
            self._runs_started += 1
            handle, report = tempfile.mkstemp(prefix='desktalk-dev-run-', suffix='.jsonl')
            os.close(handle)
            run = _Run(f'run-{self._runs_started}', what, self._clock.now(), self._clock.monotonic(), steps,
                       shows_steps, Path(report), Printout(self._settings.output_keep_kb * 1024))
            self._run = run
        log.info('Developer page: running %s (%s)', what, run.run_id)
        threading.Thread(target=self._go, args=(run,), name=f'dev-{run.run_id}', daemon=True).start()
        threading.Thread(target=self._watch_the_time, args=(run,), name=f'dev-{run.run_id}-time',
                         daemon=True).start()
        return run.run_id

    def _watch_the_time(self, run: _Run) -> None:
        """The run's second thread: stops the run at the time limit, even when nobody asks for its status."""
        while not run.over.wait(self._settings.poll_sec):
            self._stop_if_too_long(run)

    def _go(self, run: _Run) -> None:
        """The run's thread: each step in turn, reading its printout until it ends."""
        try:
            self._go_through_steps(run)
        finally:
            run.over.set()

    def _go_through_steps(self, run: _Run) -> None:
        env = {**os.environ, pytest_report.REPORT_FILE: str(run.report)}
        for step in run.steps:
            with self._lock:            # so stopping either sees the process or stops it from starting
                if run.state != 'running':
                    break
                step.state = 'running'
                run.output.add(f'$ {shlex.join(step.command)}\n')
                try:
                    process = subprocess.Popen(list(step.command), cwd=self._project, env=env,
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                except OSError as error:
                    step.state = 'failed'
                    run.output.add(f'{error}\n')
                    continue
                run.process = process
            self._read_output(run, process)
            exit_code = process.wait()
            with self._lock:
                run.process = None
                if run.state == 'running':
                    step.exit_code = exit_code
                    step.state = 'passed' if exit_code == 0 else 'failed'
        with self._lock:
            self._read_report(run)
            if run.state == 'running':
                run.state = 'passed' if all(step.exit_code == 0 for step in run.steps) else 'failed'
                run.finished = self._clock.now()
            log.info('Developer page: %s %s', run.run_id, run.state)
            run.report.unlink(missing_ok=True)

    def _read_output(self, run: _Run, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
            return
        decoder = codecs.getincrementaldecoder(ENCODING)(errors='replace')
        handle = process.stdout.fileno()
        while chunk := os.read(handle, io.DEFAULT_BUFFER_SIZE):     # whatever is there: the page sees it at once
            text = decoder.decode(chunk)
            with self._lock:
                run.output.add(text)
        process.stdout.close()
        with self._lock:
            run.output.add(decoder.decode(b'', final=True))

    def _read_report(self, run: _Run) -> None:
        """Take in the plugin's new whole lines: one per finished test. (Lock held.)"""
        try:
            with run.report.open('rb') as file:
                file.seek(run.report_read)
                new = file.read()
        except FileNotFoundError:
            return
        whole = new[:new.rfind(b'\n') + 1]          # a line still being written waits for the next time
        run.report_read += len(whole)
        for line in whole.decode(ENCODING, errors='replace').splitlines():
            try:
                found = json.loads(line)
                outcome = TestOutcome(str(found['id']), str(found['outcome']), float(found['duration_sec']),
                                      str(found['message']))
            except (ValueError, KeyError, TypeError):
                log.warning('Developer page: a line the test report plugin wrote makes no sense: %r', line)
                continue
            run.outcomes[outcome.id] = outcome      # a teardown error comes after the test's own line

    # ── stopping ──────────────────────────────────────────────────
    def _stop_if_too_long(self, run: _Run) -> None:
        if run.state == 'running' and \
                self._clock.monotonic() - run.started_mono > self._settings.run_timeout_min * 60:
            log.warning('Developer page: %s took longer than %g minutes (dev.run_timeout_min), so it was stopped',
                        run.run_id, self._settings.run_timeout_min)
            self._end(run, 'timed out')

    def _end(self, run: _Run, state: str) -> None:
        """Stop a run that is still going: its state becomes `state`, and its process is ended."""
        with self._lock:
            if run.state != 'running':
                return
            run.state = state
            run.finished = self._clock.now()
            for step in run.steps:
                if step.state in STILL_GOING:
                    step.state = state
            process = run.process
        if process is not None:
            self._end_process(process)

    def _end_process(self, process: subprocess.Popen[bytes]) -> None:
        """Ask the process and everything it started to end; after stop_wait_sec, make them."""
        self._signal(process, signal.SIGTERM)
        try:
            process.wait(timeout=self._stop_wait_sec)
        except subprocess.TimeoutExpired:
            self._signal(process, signal.SIGKILL)
            process.wait()

    @staticmethod
    def _signal(process: subprocess.Popen[bytes] | subprocess.Popen[str], which: signal.Signals) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):     # it has just ended by itself
            os.killpg(process.pid, which)       # its own process group (start_new_session), so its children too

    # ── helpers ───────────────────────────────────────────────────
    def _find(self, run_id: str) -> _Run:
        with self._lock:
            if self._run is None or self._run.run_id != run_id:
                raise KeyError(self._words.unknown_run.format(run_id=run_id))
            return self._run

    def _list_tests(self, *markers: str) -> list[list[str]]:
        """The ids pytest collects with each `-m markers`, all asked for at the same time. Raises RuntimeError
        (prompts.yaml → dev.cannot_list_tests) when a command can't start or they take longer than
        settings → dev.collect_timeout_sec; whatever is still going is then ended."""
        commands = [[*self._settings.test_command, '--collect-only', LIST_ONE_PER_LINE, '-m', m] for m in markers]
        processes: list[subprocess.Popen[str]] = []
        try:
            for command in commands:
                processes.append(subprocess.Popen(command, cwd=self._project, stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                                  encoding=ENCODING, errors='replace', start_new_session=True))
            give_up = self._clock.monotonic() + self._settings.collect_timeout_sec
            printed = [process.communicate(timeout=max(give_up - self._clock.monotonic(), 0))
                       for process in processes]
        except (OSError, subprocess.TimeoutExpired) as error:
            for process in processes:
                self._signal(process, signal.SIGKILL)
                process.communicate()
            raise RuntimeError(self._words.cannot_list_tests.format(reason=error)) from error
        found = []
        for command, process, (stdout, stderr) in zip(commands, processes, printed, strict=True):
            if process.returncode not in LISTED_FINE:
                log.warning('Developer page: listing the tests (%s) ended with %d: %s', shlex.join(command),
                            process.returncode, (stderr or stdout).strip())
            found.append([line.strip() for line in stdout.splitlines() if '::' in line and not line[:1].isspace()])
        return found

    @staticmethod
    def _with_plugin(command: tuple[str, ...]) -> tuple[str, ...]:
        """A check that runs pytest also reports each test."""
        if any(Path(part).name == 'pytest' for part in command):
            return (*command, '-p', pytest_report.__name__)
        return command
