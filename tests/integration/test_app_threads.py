"""The whole app with real threads: start() runs every background job, a picture goes all the way to the
event log, and stop() ends everything cleanly and quickly."""

from __future__ import annotations

import threading
import time

from desktalk.bootstrap import build
from desktalk.runtime import SystemClock
from tests.fakes import FakeDetector
from tests.integration.app_kit import LAPTOP, PAIR_KEY, PERSON, desk_config, desk_jpeg, inbox

JOBS = {'detection', 'scheduler', 'habit reminders', 'idle chat', 'lost items', 'lens', 'daily recap',
        'vision habits'}


def wait_until(check, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return check()


def test_start_runs_every_job_in_its_own_thread_and_stop_ends_them_quickly(tmp_path):
    config = desk_config(tmp_path)
    threads_before = set(threading.enumerate())
    app = build(config, clock=SystemClock(), detector=FakeDetector([LAPTOP, PERSON]), data_folder=tmp_path / 'run',
                pair_key=PAIR_KEY, set_up_logging=False)
    app.start()
    try:
        assert {job['name'] for job in app.supervisor.status()} == JOBS
        assert {job['state'] for job in app.supervisor.status()} == {'running'}
        assert inbox(app).push(desk_jpeg())
        assert wait_until(lambda: app.producer.pictures_analysed == 1, seconds=5)
        assert app.bus.wait_idle(timeout=5)
        day = app.event_log.days()[0]
        assert [e['detected_obj'][0]['label'] for e in app.event_log.recent_detections(day, 10)] == ['laptop']
    finally:
        started = time.monotonic()
        still_running = app.stop(timeout=5)
        took = time.monotonic() - started
    assert still_running == []
    assert took < config.camera.wait_for_picture_sec + 1          # the producer finishes waiting for a picture
    assert {job['state'] for job in app.supervisor.status()} == {'stopped'}
    assert [t.name for t in threading.enumerate() if t not in threads_before] == []      # jobs and event consumers
    assert app.store.load(f'habits/{day}')['day'] == day
    assert app.event_log.recent_detections(day, 10) == []            # compacted at stop
