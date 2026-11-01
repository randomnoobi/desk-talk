"""The weekly report and the clean-up: `python -m desktalk_cloud.reports_main`, run from the folder that holds
settings.yaml and prompts.yaml. Run exactly one: there's no step where a program claims a report, so two would both pay
the AI company for it.

One bad round never ends the loop: the job just tries again later. A stop signal ends it after the report in progress:
the report job looks for the stop before starting each one, so a stop can't outlast compose.yaml's grace period."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from desktalk_cloud.bootstrap import build_jobs, database_url, set_up_logging
from desktalk_cloud.clock import SystemClock
from desktalk_cloud.config import ConfigError, load_config
from desktalk_cloud.ports import Clock
from desktalk_cloud.store_postgres import PostgresStore, run_migrations

log = logging.getLogger(__name__)

SECONDS_IN_MINUTE = 60


class Job(Protocol):
    name: str

    def tick(self, now: float) -> float:
        """Do one round, and say how many seconds until the next."""
        ...


class StopFlag:
    """Set by a signal handler. A plain flag and short sleeps, because a threading.Event's lock could already be held by
    the code the signal interrupted."""

    def __init__(self, check_every_sec: float) -> None:
        self._check_every_sec = check_every_sec
        self._stopped = False

    def set(self) -> None:
        self._stopped = True

    def is_set(self) -> bool:
        return self._stopped

    def wait(self, seconds: float) -> bool:
        """Sleep for `seconds` or until stopped. True when stopped."""
        left = seconds
        while left > 0 and not self._stopped:
            nap = min(left, self._check_every_sec)
            time.sleep(nap)
            left -= nap
        return self._stopped


def run_jobs(jobs: Sequence[Job], clock: Clock, wait: Callable[[float], bool], retry_sec: float) -> None:
    """Tick each job when it's due, until `wait` says to stop. A job that raises is tried again after `retry_sec`."""
    next_at = {job.name: clock.now() for job in jobs}
    while True:
        for job in jobs:
            if next_at[job.name] <= clock.now():
                try:
                    next_at[job.name] = clock.now() + job.tick(clock.now())
                except Exception as error:      # its text can hold what Postgres said: only the kind is logged
                    log.warning('The job "%s" hit a problem (%s); it will try again', job.name, type(error).__name__)
                    next_at[job.name] = clock.now() + retry_sec
        if wait(max(0.0, min(next_at.values()) - clock.now())):
            return


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ready-file', type=Path, help='made once the database is ready, for the health check')
    ready_file: Path | None = parser.parse_args(argv).ready_file
    set_up_logging()
    if ready_file is not None:
        ready_file.unlink(missing_ok=True)      # a restart keeps the container's files: the last run's says nothing
    clock = SystemClock()
    try:
        config = load_config(Path.cwd(), os.environ)
        url = database_url(config)
        store = PostgresStore(url)
        stop = StopFlag(config.reports.stop_check_sec)
        jobs = build_jobs(config, store, clock, stop.is_set)
    except ConfigError as error:
        log.error('%s', error)
        return 1
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, lambda *_: stop.set())
    try:
        run_migrations(url)
        if ready_file is not None:
            ready_file.touch()
        run_jobs(jobs, clock, stop.wait, config.reports.check_every_min * SECONDS_IN_MINUTE)
    except Exception as error:                  # its text can hold what Postgres said: only the kind is logged
        log.error('Could not get the database ready (%s). Is Postgres running?', type(error).__name__)
        return 1
    finally:
        store.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
