"""The Scheduler: "run this job every N seconds", as a PeriodicWorker. Tests call tick() with made-up times."""

from __future__ import annotations

import logging
import threading

import pytest

from desktalk.runtime.scheduler import Scheduler
from desktalk.runtime.supervisor import Supervisor
from tests.fakes import FakeClock

LONGEST_WAIT = 60.0


def scheduler_at(start):
    return Scheduler(FakeClock(start=start), longest_wait_sec=LONGEST_WAIT)


def test_a_job_runs_when_it_is_due_and_then_every_n_seconds_after():
    sched = scheduler_at(1000.0)
    runs = []
    sched.every(600, lambda: runs.append('compact'), 'compact event log')
    for now in (1000, 1599, 1600, 1700, 2199, 2200):
        sched.tick(now)
        runs.append(now)
    assert runs == [1000, 1599, 'compact', 1600, 1700, 2199, 'compact', 2200]


def test_tick_returns_the_seconds_until_the_next_job_is_due():
    sched = scheduler_at(1000.0)
    sched.every(600, lambda: None, 'compact event log')
    sched.every(45, lambda: None, 'save habit day')
    assert sched.tick(1000) == 45
    assert sched.tick(1030) == 15
    assert sched.tick(1045) == 45          # 'save habit day' ran; next at 1090


def test_tick_never_asks_to_wait_longer_than_the_longest_wait():
    sched = scheduler_at(1000.0)
    assert sched.tick(1000) == LONGEST_WAIT                  # no jobs yet: one added later still gets checked
    sched.every(3600, lambda: None, 'delete old event-log days')
    assert sched.tick(1000) == LONGEST_WAIT


def test_run_now_runs_the_job_on_the_first_tick():
    sched = scheduler_at(1000.0)
    runs = []
    sched.every(600, lambda: runs.append('compact'), 'compact event log', run_now=True)
    sched.tick(1000)
    assert runs == ['compact']


def test_a_late_tick_runs_a_job_once_and_counts_the_next_turn_from_then():
    sched = scheduler_at(1000.0)
    runs = []
    sched.every(600, lambda: runs.append('compact'), 'compact event log')
    assert sched.tick(5000) == LONGEST_WAIT                  # six turns late
    assert runs == ['compact']
    sched.tick(5599)
    assert runs == ['compact']
    sched.tick(5600)
    assert runs == ['compact', 'compact']


def broken():
    raise OSError('disk full')


def test_a_failing_job_is_logged_with_its_name_and_the_error(caplog):
    sched = scheduler_at(1000.0)
    sched.every(600, broken, 'compact event log', run_now=True)
    with caplog.at_level(logging.WARNING, logger='desktalk.runtime.scheduler'):
        sched.tick(1000)
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert 'compact event log' in record.getMessage() and 'OSError: disk full' in record.getMessage()


def test_a_failing_job_stays_scheduled_and_the_other_jobs_still_run():
    sched = scheduler_at(1000.0)
    runs = []

    def flaky():
        runs.append('compact')
        raise OSError('disk full')

    sched.every(600, flaky, 'compact event log', run_now=True)
    sched.every(600, lambda: runs.append('save'), 'save habit day', run_now=True)
    assert sched.tick(1000) == LONGEST_WAIT
    sched.tick(1600)
    assert runs == ['compact', 'save', 'compact', 'save']


def test_status_lists_each_job_with_its_last_run_last_error_and_next_run():
    clock = FakeClock(start=1000.0)
    sched = Scheduler(clock, longest_wait_sec=LONGEST_WAIT)
    sched.every(600, broken, 'compact event log', run_now=True)
    sched.every(60, lambda: None, 'save habit day')
    sched.tick(1000)
    clock.advance(30)
    assert sched.status() == [
        {'name': 'compact event log', 'every_sec': 600.0, 'runs': 1, 'last_run_at': 1000.0,
         'last_error': 'OSError: disk full', 'next_run_at': 1600.0, 'next_in_sec': 570},
        {'name': 'save habit day', 'every_sec': 60.0, 'runs': 0, 'last_run_at': None,
         'last_error': None, 'next_run_at': 1060.0, 'next_in_sec': 30},
    ]


def test_a_job_that_works_again_clears_its_last_error():
    sched = scheduler_at(1000.0)
    outcomes = [OSError('disk full'), None]

    def sometimes():
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome

    sched.every(600, sometimes, 'compact event log', run_now=True)
    sched.tick(1000)
    sched.tick(1600)
    [job] = sched.status()
    assert (job['runs'], job['last_error']) == (2, None)


def test_the_traceback_is_logged_only_when_a_job_starts_failing(caplog):
    sched = scheduler_at(1000.0)
    sched.every(600, broken, 'compact event log', run_now=True)
    with caplog.at_level(logging.WARNING, logger='desktalk.runtime.scheduler'):
        sched.tick(1000)
        sched.tick(1600)
    assert [bool(r.exc_info) for r in caplog.records] == [True, False]


@pytest.mark.parametrize('seconds', [0, -5])
def test_a_job_must_wait_more_than_zero_seconds_between_runs(seconds):
    sched = scheduler_at(1000.0)
    with pytest.raises(ValueError, match='save habit day'):
        sched.every(seconds, lambda: None, 'save habit day')


def test_the_supervisor_runs_the_scheduler_like_any_other_worker():
    clock = FakeClock(start=1000.0)
    sched = Scheduler(clock, longest_wait_sec=3600)
    ran = threading.Event()
    sched.every(600, ran.set, 'compact event log', run_now=True)
    supervisor = Supervisor(clock, first_retry_sec=3600, max_retry_sec=3600)
    supervisor.add(sched)
    supervisor.start()
    try:
        assert ran.wait(2.0)
    finally:
        assert supervisor.stop(timeout=2.0) == []
    [worker] = supervisor.status()
    assert (worker['name'], worker['total_failures'], worker['last_ok_at']) == ('scheduler', 0, 1000.0)
