"""The whole app with real threads: start() runs every background job, a picture goes all the way to the
event log, and stop() ends everything cleanly and quickly."""

from __future__ import annotations

import threading
import time

from desktalk.adapters.notify import MacNotifications
from desktalk.bootstrap import build
from desktalk.runtime import SystemClock
from tests.fakes import FakeDetector
from tests.integration.app_kit import LAPTOP, PAIR_KEY, PERSON, desk_config, desk_jpeg, event_log_file, inbox

JOBS = {'detection', 'scheduler', 'rule engine', 'habit reminders', 'idle chat', 'lost items', 'lens',
        'daily recap'}


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
    shown: list[list[str]] = []                 # the Mac pop-ups' own thread runs, but nothing really pops up
    desktop = MacNotifications(config.notifications, queue_size=config.notifications.queue_size,
                               runner=lambda command, **_: shown.append(command))
    app = build(config, clock=SystemClock(), detector=FakeDetector([LAPTOP, PERSON]), data_folder=tmp_path / 'run',
                pair_key=PAIR_KEY, set_up_logging=False, desktop=desktop)
    app.start()
    try:
        assert {job['name'] for job in app.supervisor.status()} == JOBS
        assert {job['state'] for job in app.supervisor.status()} == {'running'}
        assert inbox(app).push(desk_jpeg())
        assert wait_until(lambda: app.producer.pictures_analysed == 1, seconds=5)
        assert app.bus.wait_idle(timeout=5)
        day = app.event_log.days()[0]
        assert [e['detected_obj'][0]['label'] for e in event_log_file(app).recent_detections(day, 10)] == ['laptop']
    finally:
        started = time.monotonic()
        still_running = app.stop(timeout=5)
        took = time.monotonic() - started
    assert still_running == []
    assert took < config.camera.wait_for_picture_sec + 1          # the producer finishes waiting for a picture
    assert {job['state'] for job in app.supervisor.status()} == {'stopped'}
    # jobs, event consumers and the pop-ups' thread
    assert [t.name for t in threading.enumerate() if t not in threads_before] == []
    assert app.store.load(f'habits/{day}')['day'] == day
    assert event_log_file(app).recent_detections(day, 10) == []            # compacted at stop
