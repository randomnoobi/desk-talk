"""
Keeps the background workers (reminders, Lens, idle chat, the scheduler ...) running.

    supervisor = Supervisor(SystemClock(), first_retry_sec=1, max_retry_sec=300)
    supervisor.add(reminders, start_after=30)     # any PeriodicWorker (desktalk/ports.py)
    supervisor.start()                            # one thread per worker
    supervisor.status()                           # for /api/health
    supervisor.stop(timeout=5)                    # newest first; returns the ones that didn't stop

Each worker's tick() runs over and over in its own thread, with the wait it asks for in between.
When a round fails, the error is logged with the worker's name and the round is tried again after
first_retry_sec, then twice that, and so on up to max_retry_sec. One good round starts the count over.
It never gives up, so one bad AI reply can't switch a feature off until the app restarts.

GuardedWorker is the part that decides what happens after each round; it has no thread, so tests
call step() directly with a FakeClock.
"""

from __future__ import annotations

import logging
import threading

from desktalk.ports import Clock, PeriodicWorker
from desktalk.runtime.errors import plain_error
from desktalk_shared.backoff import Backoff

log = logging.getLogger(__name__)

# A worker's state in status()
RUNNING = 'running'
WAITING_AFTER_ERROR = 'waiting after error'
STOPPED = 'stopped'


class GuardedWorker:
    """One worker, one round at a time: runs tick(), catches errors, works out the next wait, keeps count."""

    def __init__(self, worker: PeriodicWorker, clock: Clock, *, first_retry_sec: float, max_retry_sec: float):
        self.worker = worker
        self.name = worker.name
        self._clock = clock
        self._lock = threading.Lock()          # status() is read from the web server's thread
        self._backoff = Backoff(first_retry_sec, max_retry_sec)
        self._failures_in_row = 0
        self._total_failures = 0
        self._last_error: str | None = None
        self._last_ok_at: float | None = None

    def step(self) -> float:
        """Run one round. Returns how many seconds to wait before the next one."""
        now = self._clock.now()
        try:
            wait = self.worker.tick(now)
            if isinstance(wait, bool) or not isinstance(wait, int | float):
                raise TypeError(f'tick() returned {wait!r} instead of the seconds to wait')
        except Exception as exc:
            with self._lock:
                self._failures_in_row += 1
                self._total_failures += 1
                self._last_error = plain_error(exc)
                in_row, retry = self._failures_in_row, self._backoff.next()
            # The full traceback only when it starts failing, so a long outage doesn't flood the log.
            log.error('%s failed (%d in a row): %s. Trying again in %g s', self.name, in_row, plain_error(exc),
                      retry, exc_info=in_row == 1)
            return retry
        with self._lock:
            failed_before = self._failures_in_row
            self._backoff.reset()
            self._failures_in_row = 0
            self._last_ok_at = now
        if failed_before:
            log.info('%s is working again after %d failed rounds', self.name, failed_before)
        return wait

    def status(self) -> dict:
        with self._lock:
            return {'name': self.name, 'failures_in_row': self._failures_in_row,
                    'total_failures': self._total_failures, 'last_error': self._last_error,
                    'last_ok_at': self._last_ok_at}


class _Slot:
    """One worker inside the Supervisor: its guard, its thread and its own stop signal."""

    def __init__(self, guarded: GuardedWorker, start_after: float):
        self.guarded = guarded
        self.start_after = float(start_after)
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None


class Supervisor:
    """Runs each background worker in its own thread and keeps it going when a round fails."""

    def __init__(self, clock: Clock, *, first_retry_sec: float, max_retry_sec: float):
        self._clock = clock
        self._first_retry = first_retry_sec
        self._max_retry = max_retry_sec
        self._slots: list[_Slot] = []
        self._started = False

    def add(self, worker: PeriodicWorker, start_after: float = 0.0) -> None:
        """Run `worker` once start() is called. `start_after`: seconds to wait before its first round."""
        if self._started:
            raise RuntimeError(f'add "{worker.name}" before start(): workers added later would never run')
        if any(slot.guarded.name == worker.name for slot in self._slots):
            raise ValueError(f'there is already a worker called "{worker.name}"')
        guarded = GuardedWorker(worker, self._clock, first_retry_sec=self._first_retry,
                                max_retry_sec=self._max_retry)
        self._slots.append(_Slot(guarded, start_after))

    def start(self) -> None:
        """Start every worker, each in its own thread, in the order they were added."""
        if self._started:
            raise RuntimeError('start() was already called')
        self._started = True
        for slot in self._slots:
            slot.thread = threading.Thread(target=self._run, args=(slot,), name=slot.guarded.name, daemon=True)
            slot.thread.start()

    def stop(self, timeout: float) -> list[str]:
        """Stop the workers one at a time, newest first, waiting up to `timeout` seconds for each.
        A worker waiting between rounds stops at once; one in the middle of a round finishes it first.
        Returns the names of the workers still running after their wait (their threads are daemons,
        so they don't keep the app from closing)."""
        still_running = []
        for slot in reversed(self._slots):
            slot.stopping.set()
            if slot.thread:
                slot.thread.join(timeout)
                if slot.thread.is_alive():
                    still_running.append(slot.guarded.name)
        return still_running

    def status(self) -> list[dict]:
        """One entry per worker, in the order they were added (for /api/health):
        name, state ('running', 'waiting after error' or 'stopped'), failures_in_row, total_failures,
        last_error (plain words, or None) and last_ok_at (seconds since 1970, or None)."""
        out = []
        for slot in self._slots:
            info = slot.guarded.status()
            if slot.thread is None or not slot.thread.is_alive():
                state = STOPPED
            elif info['failures_in_row']:
                state = WAITING_AFTER_ERROR
            else:
                state = RUNNING
            out.append({'name': info.pop('name'), 'state': state, **info})
        return out

    @staticmethod
    def _run(slot: _Slot) -> None:
        wait = slot.start_after
        while not slot.stopping.wait(wait):
            wait = slot.guarded.step()
