"""The Scheduler: "run this job every N seconds", as a PeriodicWorker. Tests move a FakeClock to made-up times and
tick. Its turns go by the monotonic clock, so a wall clock set back (or forward) never stops or rushes them."""

from __future__ import annotations

import logging
import threading

import pytest

from desktalk.runtime.scheduler import Scheduler
from desktalk.runtime.supervisor import Supervisor
from tests.fakes import FakeClock

LONGEST_WAIT = 60.0


def scheduler_at(start):
    """A scheduler, and at(t): move its clock on to wall time t and tick."""
    clock = FakeClock(start=start)
    sched = Scheduler(clock, longest_wait_sec=LONGEST_WAIT)

    def at(t):
        clock.advance(t - clock.now())
        return sched.tick(clock.now())

    return sched, at


def test_a_job_runs_when_it_is_due_and_then_every_n_seconds_after():
    sched, at = scheduler_at(1000.0)
    runs = []
    sched.every(600, lambda: runs.append('compact'), 'compact event log')
    for now in (1000, 1599, 1600, 1700, 2199, 2200):
        at(now)
        runs.append(now)
    assert runs == [1000, 1599, 'compact', 1600, 1700, 2199, 'compact', 2200]


def test_tick_returns_the_seconds_until_the_next_job_is_due():
    sched, at = scheduler_at(1000.0)
    sched.every(600, lambda: None, 'compact event log')
    sched.every(45, lambda: None, 'save habit day')
    assert at(1000) == 45
    assert at(1030) == 15
    assert at(1045) == 45          # 'save habit day' ran; next at 1090


def test_tick_never_asks_to_wait_longer_than_the_longest_wait():
    sched, at = scheduler_at(1000.0)
    assert at(1000) == LONGEST_WAIT                  # no jobs yet: one added later still gets checked
    sched.every(3600, lambda: None, 'delete old event-log days')
    assert at(1000) == LONGEST_WAIT


def test_run_now_runs_the_job_on_the_first_tick():
    sched, at = scheduler_at(1000.0)
    runs = []
    sched.every(600, lambda: runs.append('compact'), 'compact event log', run_now=True)
    at(1000)
    assert runs == ['compact']


def test_a_late_tick_runs_a_job_once_and_counts_the_next_turn_from_then():
    sched, at = scheduler_at(1000.0)
    runs = []
    sched.every(600, lambda: runs.append('compact'), 'compact event log')
    assert at(5000) == LONGEST_WAIT                  # six turns late
    assert runs == ['compact']
    at(5599)
    assert runs == ['compact']
    at(5600)
    assert runs == ['compact', 'compact']


def broken():
    raise OSError('disk full')


def test_a_failing_job_is_logged_with_its_name_and_the_error(caplog):
    sched, at = scheduler_at(1000.0)
    sched.every(600, broken, 'compact event log', run_now=True)
    with caplog.at_level(logging.WARNING, logger='desktalk.runtime.scheduler'):
        at(1000)
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert 'compact event log' in record.getMessage() and 'OSError: disk full' in record.getMessage()


def test_a_failing_job_stays_scheduled_and_the_other_jobs_still_run():
    sched, at = scheduler_at(1000.0)
    runs = []

    def flaky():
        runs.append('compact')
        raise OSError('disk full')

    sched.every(600, flaky, 'compact event log', run_now=True)
    sched.every(600, lambda: runs.append('save'), 'save habit day', run_now=True)
    assert at(1000) == LONGEST_WAIT
    at(1600)
    assert runs == ['compact', 'save', 'compact', 'save']


def test_status_lists_each_job_with_its_last_run_last_error_and_next_run():
    clock = FakeClock(start=1000.0)
    sched = Scheduler(clock, longest_wait_sec=LONGEST_WAIT)
    sched.every(600, broken, 'compact event log', run_now=True)
    sched.every(60, lambda: None, 'save habit day')
    sched.tick(clock.now())
    clock.advance(30)
    assert sched.status() == [
        {'name': 'compact event log', 'every_sec': 600.0, 'runs': 1, 'last_run_at': 1000.0,
         'last_error': 'OSError: disk full', 'next_run_at': 1600.0, 'next_in_sec': 570},
        {'name': 'save habit day', 'every_sec': 60.0, 'runs': 0, 'last_run_at': None,
         'last_error': None, 'next_run_at': 1060.0, 'next_in_sec': 30},
    ]


def test_jobs_still_run_when_the_wall_clock_is_set_back():
    """The clock set back a year (by hand, or a Pi's clock put right): the turns go by the monotonic clock."""
    clock = FakeClock(start=1_760_000_000.0)
    sched = Scheduler(clock, longest_wait_sec=3600)
    runs = []
    sched.every(600, lambda: runs.append(clock.now()), 'compact event log')
    clock.advance(300)
    clock.set_wall_clock(1_760_000_000.0 - 365 * 24 * 3600)
    assert sched.tick(clock.now()) == 300
    clock.advance(300)
    assert sched.tick(clock.now()) == 600            # due 10 minutes after it was added, wall clock or not
    clock.advance(600)
    sched.tick(clock.now())
    assert len(runs) == 2


def test_a_wall_clock_set_forward_does_not_run_jobs_early():
    clock = FakeClock(start=1000.0)
    sched = Scheduler(clock, longest_wait_sec=LONGEST_WAIT)
    runs = []
    sched.every(600, lambda: runs.append('compact'), 'compact event log')
    clock.set_wall_clock(1000.0 + 365 * 24 * 3600)
    assert sched.tick(clock.now()) == LONGEST_WAIT
    assert runs == []


def test_status_shows_the_next_run_by_the_wall_clock_after_it_was_set_back():
    clock = FakeClock(start=1000.0)
    sched = Scheduler(clock, longest_wait_sec=LONGEST_WAIT)
    sched.every(600, lambda: None, 'compact event log', run_now=True)
    sched.tick(clock.now())
    clock.advance(100)
    clock.set_wall_clock(500.0)
    [job] = sched.status()
    assert (job['last_run_at'], job['next_run_at'], job['next_in_sec']) == (1000.0, 1000.0, 500)


def test_a_job_that_works_again_clears_its_last_error():
    sched, at = scheduler_at(1000.0)
    outcomes = [OSError('disk full'), None]

    def sometimes():
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome

    sched.every(600, sometimes, 'compact event log', run_now=True)
    at(1000)
    at(1600)
    [job] = sched.status()
    assert (job['runs'], job['last_error']) == (2, None)


def test_the_traceback_is_logged_only_when_a_job_starts_failing(caplog):
    sched, at = scheduler_at(1000.0)
    sched.every(600, broken, 'compact event log', run_now=True)
    with caplog.at_level(logging.WARNING, logger='desktalk.runtime.scheduler'):
        at(1000)
        at(1600)
    assert [bool(r.exc_info) for r in caplog.records] == [True, False]


@pytest.mark.parametrize('seconds', [0, -5])
def test_a_job_must_wait_more_than_zero_seconds_between_runs(seconds):
    sched, at = scheduler_at(1000.0)
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
