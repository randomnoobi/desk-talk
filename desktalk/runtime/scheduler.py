"""
Runs jobs on a timer ("compact the event log every 10 minutes").

    scheduler = Scheduler(SystemClock(), longest_wait_sec=60)
    scheduler.every(600, event_log.compact, 'compact event log')
    supervisor.add(scheduler)          # it's a PeriodicWorker: the Supervisor runs it in its own thread

Each tick() runs the jobs that are due and says how long until the next one. A job that raises is
logged and stays scheduled; the other jobs still run. A late tick runs a job once (no catch-up runs)
and its next turn counts from then.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from desktalk.ports import Clock
from desktalk.runtime.errors import plain_error

log = logging.getLogger(__name__)


class _Job:
    def __init__(self, seconds: float, run: Callable[[], object], name: str, next_run: float):
        self.seconds = float(seconds)
        self.run = run
        self.name = name
        self.next_run = next_run
        self.runs = 0
        self.last_run: float | None = None
        self.last_error: str | None = None


class Scheduler:
    """A PeriodicWorker that runs each job every N seconds.
    longest_wait_sec: never wait longer than this between checks, so a job added later still starts on time."""

    def __init__(self, clock: Clock, *, longest_wait_sec: float, name: str = 'scheduler'):
        self.name = name
        self._clock = clock
        self._longest_wait = float(longest_wait_sec)
        self._lock = threading.Lock()          # status() is read from the web server's thread
        self._jobs: list[_Job] = []

    def every(self, seconds: float, job: Callable[[], object], name: str, run_now: bool = False) -> None:
        """Run `job()` every `seconds`. The first run is `seconds` from now, or at the next tick with run_now."""
        if seconds <= 0:
            raise ValueError(f'job "{name}": the time between runs must be more than 0 seconds, not {seconds}')
        now = self._clock.now()
        with self._lock:
            self._jobs.append(_Job(seconds, job, name, now if run_now else now + seconds))

    def tick(self, now: float) -> float:
        """Run the jobs that are due. Returns the seconds until the next one is due."""
        with self._lock:
            jobs = list(self._jobs)
        for job in jobs:
            if now < job.next_run:
                continue
            error = None
            try:
                job.run()
            except Exception as exc:
                error = plain_error(exc)
                # The full traceback only when it starts failing, so a long outage doesn't flood the log.
                log.error('Job "%s" failed: %s', job.name, error, exc_info=job.last_error is None)
            with self._lock:
                job.runs += 1
                job.last_run = now
                job.last_error = error
                job.next_run = now + job.seconds
        return min([job.next_run - now for job in jobs] + [self._longest_wait])

    def status(self) -> list[dict]:
        """One entry per job: name, every_sec, runs, last_run_at, last_error (plain words, or None since its
        last good run), next_run_at (seconds since 1970) and next_in_sec."""
        now = self._clock.now()
        with self._lock:
            return [{'name': j.name, 'every_sec': j.seconds, 'runs': j.runs, 'last_run_at': j.last_run,
                     'last_error': j.last_error, 'next_run_at': j.next_run,
                     'next_in_sec': max(0, round(j.next_run - now))} for j in self._jobs]
