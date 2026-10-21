"""The Supervisor: runs background workers, keeps them going when a round fails.

GuardedWorker (one round at a time) is tested with FakeClock and no threads.
The few threaded tests at the bottom use short real timeouts and never sleep for a fixed time.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from desktalk.runtime.supervisor import GuardedWorker, Supervisor
from tests.fakes import FakeClock


class ScriptedWorker:
    """A worker whose rounds follow a script: a number is the wait it returns, an exception is raised.
    The last step repeats forever."""

    def __init__(self, name, *script):
        self.name = name
        self.script = list(script)
        self.ticks: list[float] = []

    def tick(self, now):
        self.ticks.append(now)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, BaseException):
            raise step
        return step


def guard(worker, clock=None, first_retry_sec=1.0, max_retry_sec=10.0):
    return GuardedWorker(worker, clock or FakeClock(), first_retry_sec=first_retry_sec, max_retry_sec=max_retry_sec)


# ── one round at a time (no threads) ─────────────────────────────────
def test_a_good_round_gets_the_clock_time_and_waits_what_the_worker_asked():
    clock = FakeClock(start=1_700_000_000.0)
    worker = ScriptedWorker('lens', 30.0)
    assert guard(worker, clock).step() == 30.0
    assert worker.ticks == [1_700_000_000.0]


def test_a_failed_round_waits_the_first_retry_time_not_the_workers_wait():
    worker = ScriptedWorker('lens', RuntimeError('no picture'))
    assert guard(worker, first_retry_sec=1.0).step() == 1.0


def test_the_retry_wait_doubles_then_stops_at_the_max():
    g = guard(ScriptedWorker('lens', RuntimeError('no picture')), first_retry_sec=1.0, max_retry_sec=10.0)
    assert [g.step() for _ in range(6)] == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def test_one_good_round_resets_the_retry_wait():
    oops = RuntimeError('no picture')
    g = guard(ScriptedWorker('lens', oops, oops, 30.0, oops), first_retry_sec=1.0, max_retry_sec=10.0)
    assert [g.step() for _ in range(4)] == [1.0, 2.0, 30.0, 1.0]


def test_status_counts_failures_in_a_row_and_in_total():
    oops = RuntimeError('no picture')
    g = guard(ScriptedWorker('lens', oops, oops, 30.0, oops))
    for _ in range(4):
        g.step()
    status = g.status()
    assert (status['name'], status['failures_in_row'], status['total_failures']) == ('lens', 1, 3)


def test_status_gives_the_last_error_in_plain_words():
    g = guard(ScriptedWorker('lens', ValueError('the camera is off')))
    g.step()
    assert g.status()['last_error'] == 'ValueError: the camera is off'


def test_status_says_when_it_last_ran_ok():
    clock = FakeClock(start=1_700_000_000.0)
    g = guard(ScriptedWorker('lens', 30.0, RuntimeError('no picture')), clock)
    assert g.status()['last_ok_at'] is None
    g.step()
    clock.advance(30)
    g.step()
    assert g.status()['last_ok_at'] == 1_700_000_000.0


def test_a_failed_round_is_logged_with_the_worker_name_and_the_error(caplog):
    g = guard(ScriptedWorker('idle chat', ValueError('the AI said nothing')))
    with caplog.at_level(logging.WARNING, logger='desktalk.runtime.supervisor'):
        g.step()
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert 'idle chat' in record.getMessage() and 'ValueError: the AI said nothing' in record.getMessage()


def test_the_traceback_is_logged_only_when_a_worker_starts_failing(caplog):
    g = guard(ScriptedWorker('idle chat', ValueError('the AI said nothing')))
    with caplog.at_level(logging.WARNING, logger='desktalk.runtime.supervisor'):
        g.step()
        g.step()
    assert [bool(r.exc_info) for r in caplog.records] == [True, False]


def test_working_again_after_failures_is_logged(caplog):
    oops = RuntimeError('no picture')
    g = guard(ScriptedWorker('lens', oops, oops, 30.0))
    g.step()
    g.step()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger='desktalk.runtime.supervisor'):
        g.step()
    [record] = caplog.records
    assert record.levelno == logging.INFO
    assert 'lens' in record.getMessage() and '2' in record.getMessage()


def test_a_round_that_returns_no_wait_counts_as_failed():
    # Today's tick() methods return None; waiting "None" seconds would mean waiting forever.
    g = guard(ScriptedWorker('reminders', None), first_retry_sec=1.0)
    assert g.step() == 1.0
    assert g.status()['failures_in_row'] == 1
    assert 'None' in g.status()['last_error']


def test_it_never_gives_up_even_after_thousands_of_failures():
    # About a week of failing once a minute. A wait worked out as 2 ** failures would overflow long before.
    worker = ScriptedWorker('lens', *[RuntimeError('no picture')] * 10_000, 30.0)
    g = guard(worker, first_retry_sec=1.0, max_retry_sec=60.0)
    waits = [g.step() for _ in range(10_000)]
    assert waits[-1] == 60.0
    assert g.step() == 30.0
    assert len(worker.ticks) == 10_001


# ── the threads (short real timeouts, no fixed sleeps) ───────────────
WAIT_LIMIT = 2.0       # seconds a threaded test waits for something that should happen almost at once


def wait_until(condition, limit=WAIT_LIMIT):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.002)
    return condition()


class CountingWorker:
    """Returns `wait` every round and remembers which thread ran each round, and when (real time)."""

    def __init__(self, name, wait=0.001):
        self.name = name
        self.wait = wait
        self.threads: list[str] = []
        self.times: list[float] = []

    def tick(self, now):
        self.threads.append(threading.current_thread().name)
        self.times.append(time.monotonic())
        return self.wait


@pytest.fixture
def supervisor():
    sup = Supervisor(FakeClock(), first_retry_sec=0.001, max_retry_sec=0.001)
    yield sup
    sup.stop(timeout=WAIT_LIMIT)


def test_each_worker_runs_over_and_over_in_its_own_thread(supervisor):
    lens, chat = CountingWorker('lens'), CountingWorker('idle chat')
    supervisor.add(lens)
    supervisor.add(chat)
    supervisor.start()
    assert wait_until(lambda: len(lens.threads) >= 3 and len(chat.threads) >= 3)
    assert set(lens.threads) == {'lens'} and set(chat.threads) == {'idle chat'}


class StuckWorker:
    """Its round doesn't finish until the test lets it go."""

    def __init__(self, name):
        self.name = name
        self.started = threading.Event()
        self.let_go = threading.Event()

    def tick(self, now):
        self.started.set()
        self.let_go.wait()
        return 0.001


def test_stop_names_the_workers_that_did_not_stop_in_time(supervisor):
    stuck, fine = StuckWorker('daily recap'), CountingWorker('lens')
    supervisor.add(stuck)
    supervisor.add(fine)
    supervisor.start()
    try:
        assert stuck.started.wait(WAIT_LIMIT)
        assert supervisor.stop(timeout=0.05) == ['daily recap']
    finally:
        stuck.let_go.set()


def test_stop_interrupts_a_long_wait_at_once(supervisor):
    hourly = CountingWorker('daily recap', wait=3600)
    supervisor.add(hourly)
    supervisor.start()
    assert wait_until(lambda: hourly.threads)
    assert supervisor.stop(timeout=WAIT_LIMIT) == []


def test_stop_goes_in_reverse_start_order(supervisor):
    first, last = CountingWorker('scheduler'), StuckWorker('idle chat')
    supervisor.add(first)
    supervisor.add(last)
    supervisor.start()
    try:
        assert last.started.wait(WAIT_LIMIT)
        asked_to_stop = time.monotonic()
        supervisor.stop(timeout=0.3)
        # 'idle chat' is asked first and given 0.3 s; 'scheduler' keeps going until then.
        assert first.times[-1] > asked_to_stop + 0.1
    finally:
        last.let_go.set()


def states(supervisor):
    return {w['name']: w['state'] for w in supervisor.status()}


def test_status_says_running_while_it_runs_and_stopped_before_and_after(supervisor):
    supervisor.add(CountingWorker('lens', wait=3600))
    assert states(supervisor) == {'lens': 'stopped'}
    supervisor.start()
    assert wait_until(lambda: states(supervisor) == {'lens': 'running'})
    supervisor.stop(timeout=WAIT_LIMIT)
    assert states(supervisor) == {'lens': 'stopped'}


def test_status_says_waiting_after_error_while_a_failed_worker_waits_to_try_again():
    supervisor = Supervisor(FakeClock(), first_retry_sec=3600, max_retry_sec=3600)
    supervisor.add(ScriptedWorker('reminders', RuntimeError('no picture')))
    supervisor.add(CountingWorker('lens', wait=3600))
    supervisor.start()
    try:
        assert wait_until(lambda: states(supervisor) == {'reminders': 'waiting after error', 'lens': 'running'})
    finally:
        supervisor.stop(timeout=WAIT_LIMIT)


def test_a_failing_worker_is_tried_again_in_its_thread_until_it_works(supervisor):
    oops = RuntimeError('no picture')
    supervisor.add(ScriptedWorker('lens', oops, oops, 3600.0))
    supervisor.start()
    assert wait_until(lambda: supervisor.status()[0]['last_ok_at'] is not None)
    [lens] = supervisor.status()
    assert lens == {'name': 'lens', 'state': 'running', 'failures_in_row': 0, 'total_failures': 2,
                    'last_error': 'RuntimeError: no picture', 'last_ok_at': 1_760_000_000.0}


def test_start_after_holds_back_the_first_round(supervisor):
    later, now = CountingWorker('daily recap'), CountingWorker('lens')
    supervisor.add(later, start_after=3600)
    supervisor.add(now)
    supervisor.start()
    assert wait_until(lambda: len(now.threads) >= 20)      # plenty of time for 'daily recap' to have run, if it would
    assert later.threads == []
    assert supervisor.stop(timeout=WAIT_LIMIT) == []


def test_two_workers_with_the_same_name_are_refused(supervisor):
    supervisor.add(CountingWorker('lens'))
    with pytest.raises(ValueError, match='lens'):
        supervisor.add(CountingWorker('lens'))


def test_a_worker_added_after_start_is_refused_instead_of_never_running(supervisor):
    supervisor.start()
    with pytest.raises(RuntimeError):
        supervisor.add(CountingWorker('lens'))


def test_starting_twice_is_refused_instead_of_running_every_worker_twice(supervisor):
    supervisor.add(CountingWorker('lens', wait=3600))
    supervisor.start()
    with pytest.raises(RuntimeError):
        supervisor.start()
