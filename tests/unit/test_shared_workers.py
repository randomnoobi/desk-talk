"""WorkerThread: runs one job over and over in a thread of its own and starts it again after a crash."""

from __future__ import annotations

import logging
import threading

from desktalk_shared.backoff import Backoff
from desktalk_shared.workers import WorkerThread


def test_worker_thread_restarts_after_a_crash(caplog):
    calls = []
    done = threading.Event()

    def step():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError('one bad round')
        done.set()
        return None

    thread = WorkerThread('test', step, Backoff(first=0.01, most=0.02))
    with caplog.at_level(logging.ERROR):
        thread.start()
        assert done.wait(2)
        thread.stop()
        thread.join(2)
    assert not thread.is_alive()
    crashes = [r for r in caplog.records if r.name == 'desktalk_shared.workers']
    assert len(calls) == 3 and len(crashes) == 2
    assert 'test had a problem' in crashes[0].getMessage() and 'one bad round' in caplog.text


def test_the_wait_after_a_crash_grows_and_one_good_round_resets_it():
    results: list = [RuntimeError('a'), RuntimeError('b'), RuntimeError('c'), 7.0, RuntimeError('d')]

    def step():
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    thread = WorkerThread('test', step, Backoff(first=1, most=3))
    assert [thread.run_once() for _ in range(5)] == [1, 2, 3, 7.0, 1]


def test_worker_thread_steps_again_when_woken_and_stops():
    steps = threading.Semaphore(0)
    wake = threading.Event()

    def step():
        steps.release()
        return None                                                       # wait until woken

    thread = WorkerThread('test', step, Backoff(first=0.01, most=0.01), wake=wake)
    thread.start()
    assert steps.acquire(timeout=2)
    wake.set()
    assert steps.acquire(timeout=2)
    thread.stop()
    thread.join(2)
    assert not thread.is_alive()


def test_without_a_wake_event_it_makes_its_own():
    thread = WorkerThread('test', lambda: None, Backoff(first=1, most=1))
    assert isinstance(thread.wake, threading.Event) and thread.daemon
