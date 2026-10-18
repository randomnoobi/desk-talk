"""
Runs jobs on a timer ("Scheduler, compact every 10" on the whiteboard).

    sched = Scheduler()
    sched.every(600, event_log.compact, 'compact event log')
    sched.start()
"""

from __future__ import annotations

import threading
import time
from typing import Callable


class Job:
    def __init__(self, seconds: float, fn: Callable, name: str, next_run: float):
        self.seconds = float(seconds)
        self.fn = fn
        self.name = name
        self.next_run = next_run
        self.runs = 0
        self.last_error: str | None = None


class Scheduler(threading.Thread):
    def __init__(self, tick_sec: float = 1.0, clock: Callable[[], float] = time.time):
        super().__init__(daemon=True, name='Scheduler')
        self.tick_sec = tick_sec
        self.clock = clock
        self.jobs: list[Job] = []
        self._stop_event = threading.Event()

    def every(self, seconds: float, fn: Callable, name: str, run_now: bool = False) -> Job:
        now = self.clock()
        job = Job(seconds, fn, name, now if run_now else now + seconds)
        self.jobs.append(job)
        return job

    def run_pending(self, now: float | None = None) -> list[str]:
        """Run every job that is due. Returns the names that ran (tests call this directly)."""
        now = self.clock() if now is None else now
        ran = []
        for job in self.jobs:
            if now < job.next_run:
                continue
            try:
                job.fn()
                job.last_error = None
            except Exception as exc:
                job.last_error = repr(exc)
                print(f'  [scheduler] {job.name} failed: {exc!r}')
            job.runs += 1
            job.next_run = now + job.seconds
            ran.append(job.name)
        return ran

    def run(self):
        while not self._stop_event.is_set():
            self.run_pending()
            self._stop_event.wait(self.tick_sec)

    def stop(self):
        self._stop_event.set()

    def status(self) -> list[dict]:
        now = self.clock()
        return [{'name': j.name, 'every_sec': j.seconds, 'runs': j.runs,
                 'next_in_sec': max(0, round(j.next_run - now)), 'last_error': j.last_error} for j in self.jobs]
