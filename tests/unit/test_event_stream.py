"""The event stream: producer → stream → consumers."""

import threading
import time

from desktalk.events.stream import EventStream
from desktalk.scheduler import Scheduler


def test_synchronous_delivery_in_order_and_filtered():
    s = EventStream(synchronous=True)
    got = []
    s.subscribe('all', lambda e: got.append(('all', e['type'])))
    s.subscribe('only-detection', lambda e: got.append(('det', e['type'])), types={'detection'})
    s.publish('detection', detected_obj=[])
    s.publish('alert', kind='proximity')
    assert got == [('all', 'detection'), ('det', 'detection'), ('all', 'alert')]


def test_event_shape():
    s = EventStream(synchronous=True)
    ev = s.publish('habit', ts=123.0, habit='hydration')
    assert ev['type'] == 'habit' and ev['ts'] == 123.0 and ev['habit'] == 'hydration' and ev['id']


def test_threaded_consumers_and_wait_idle():
    s = EventStream()
    got, lock = [], threading.Lock()

    def slow(e):
        time.sleep(0.01)
        with lock:
            got.append(e['n'])
    s.subscribe('slow', slow)
    for n in range(20):
        s.publish('x', n=n)
    assert s.wait_idle(5)
    assert got == list(range(20))          # each consumer sees its events in order
    s.close()


def test_a_failing_consumer_keeps_going():
    s = EventStream(synchronous=True, log_errors=False)
    seen = []

    def flaky(e):
        if e['n'] == 1:
            raise RuntimeError('boom')
        seen.append(e['n'])
    sub = s.subscribe('flaky', flaky)
    for n in range(3):
        s.publish('x', n=n)
    assert seen == [0, 2] and sub.errors == 1


def test_full_queue_drops_the_oldest():
    s = EventStream()
    block = threading.Event()
    got = []
    sub = s.subscribe('blocked', lambda e: (block.wait(), got.append(e['n'])), maxsize=3)
    for n in range(10):
        s.publish('x', n=n)
    block.set()
    assert s.wait_idle(5)
    assert sub.dropped > 0
    assert got[-1] == 9                     # the newest event always gets through
    s.close()


def test_scheduler_runs_jobs_when_due():
    clock = [1000.0]
    sched = Scheduler(clock=lambda: clock[0])
    runs = []
    sched.every(600, lambda: runs.append(clock[0]), 'compact')
    assert sched.run_pending(1000) == []
    assert sched.run_pending(1599) == []
    assert sched.run_pending(1600) == ['compact']
    assert sched.run_pending(1700) == []
    assert sched.run_pending(2200) == ['compact']
    assert len(runs) == 2


def test_scheduler_survives_a_failing_job():
    sched = Scheduler(clock=lambda: 0)
    sched.every(1, lambda: 1 / 0, 'bad', run_now=True)
    assert sched.run_pending(0) == ['bad']
    assert 'ZeroDivisionError' in sched.status()[0]['last_error']
