"""MacNotifications: the Mac's osascript, run on one background thread with a time limit; nothing here really
shows a notification (a fake runner stands in for subprocess.run)."""

from __future__ import annotations

import logging
import threading
import time

from desktalk.adapters.notify.mac import MacNotifications, NoNotifications, applescript_string, make_notifications
from desktalk.config import NotificationsSettings
from desktalk.ports import DesktopNotifications

QUEUE = 4
SETTINGS = NotificationsSettings.model_construct(enabled=True, warnings=True, reminders=True, sips=True,
                                                 time_limit_sec=5.0, command='osascript')


class Runner:
    """Stands in for subprocess.run: remembers each call, can be slow or fail."""

    def __init__(self, delay=0.0, error=None):
        self.calls: list[tuple[list[str], dict]] = []
        self.delay, self.error = delay, error
        self.done = threading.Event()
        self.threads: set[str] = set()

    def __call__(self, args, **kwargs):
        self.threads.add(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((list(args), kwargs))
        self.done.set()
        if self.error:
            raise self.error


def test_it_is_desktop_notifications():
    mac = MacNotifications(SETTINGS, queue_size=QUEUE, runner=Runner())
    assert isinstance(mac, DesktopNotifications)
    assert isinstance(NoNotifications(), DesktopNotifications)


def test_osascript_command_escapes_quotes():
    assert applescript_string('say "hi" \\ there') == '"say \\"hi\\" \\\\ there"'
    runner = Runner()
    mac = MacNotifications(SETTINGS, queue_size=QUEUE, runner=runner)
    mac.show('Cup', 'say "hi" \\ there')
    assert runner.done.wait(2)
    args, kwargs = runner.calls[0]
    assert args == ['osascript', '-e', 'display notification "say \\"hi\\" \\\\ there" with title "Cup"']
    assert kwargs['timeout'] == 5.0
    mac.close()


def test_a_new_line_stays_on_one_line_of_the_script():
    assert '\n' not in applescript_string('a\nnew line')


def test_slow_osascript_does_not_block():
    runner = Runner(delay=0.5)
    mac = MacNotifications(SETTINGS, queue_size=QUEUE, runner=runner)
    started = time.monotonic()
    mac.show('Cup', 'Time for water')
    assert time.monotonic() - started < 0.3
    assert runner.done.wait(3)
    assert threading.current_thread().name not in runner.threads       # it ran on the background thread
    mac.close()


def test_a_full_queue_drops_the_pop_up_and_says_so(caplog):
    runner = Runner(delay=0.3)
    mac = MacNotifications(SETTINGS, runner=runner, queue_size=1)
    with caplog.at_level(logging.WARNING):
        for n in range(6):
            mac.show('Cup', f'message {n}')
    assert any('dropped' in r.message.lower() for r in caplog.records)
    mac.close()
    assert 1 <= len(runner.calls) < 6


def test_runner_error_is_logged_not_raised(caplog):
    runner = Runner(error=FileNotFoundError('osascript'))
    mac = MacNotifications(SETTINGS, queue_size=QUEUE, runner=runner)
    with caplog.at_level(logging.WARNING):
        mac.show('Cup', 'one')
        mac.show('Cup', 'two')
        mac.close()
    assert len(runner.calls) == 2
    failures = [r for r in caplog.records if 'FileNotFoundError' in r.message or 'osascript' in r.message]
    assert len(failures) == 1                                       # logged once per kind of error


def test_a_different_kind_of_error_is_logged_too(caplog):
    errors = [FileNotFoundError('osascript'), FileNotFoundError('osascript'), TimeoutError('slow')]

    def runner(args, **kwargs):
        raise errors.pop(0)

    mac = MacNotifications(SETTINGS, queue_size=QUEUE, runner=runner)
    with caplog.at_level(logging.WARNING):
        for n in range(3):
            mac.show('Cup', f'message {n}')
        mac.close()
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 2


def test_not_a_mac_gives_no_notifications(caplog):
    with caplog.at_level(logging.INFO):
        notifications = make_notifications(SETTINGS, 'linux', queue_size=QUEUE)
    assert isinstance(notifications, NoNotifications) and notifications.show('Cup', 'x') is None
    assert len([r for r in caplog.records if 'only work on a Mac' in r.message]) == 1


def test_switched_off_gives_no_notifications():
    off = NotificationsSettings.model_construct(**{**SETTINGS.model_dump(), 'enabled': False})
    assert isinstance(make_notifications(off, 'darwin', queue_size=QUEUE), NoNotifications)


def test_a_mac_gets_the_real_one():
    mac = make_notifications(SETTINGS, 'darwin', queue_size=QUEUE, runner=Runner())
    assert isinstance(mac, MacNotifications)
    mac.close()


def test_no_test_ever_runs_the_real_osascript(osascript_calls):
    """The guard in tests/conftest.py: a MacNotifications made without a runner (as build() makes one) never
    reaches the real osascript in a test. The call is caught here, and would fail any other test."""
    mac = MacNotifications(SETTINGS, queue_size=QUEUE)
    mac.show('Desk Talk', 'hello')
    mac.close()
    assert [args[0][0] for args, _ in osascript_calls] == ['osascript']
    osascript_calls.clear()         # caught on purpose here, so the guard lets this test pass


def test_close_gives_up_when_the_queue_stays_full():
    """A stuck osascript: close() waits time_limit_sec for room in the queue, then leaves the thread behind."""
    quick = NotificationsSettings.model_construct(**{**SETTINGS.model_dump(), 'time_limit_sec': 0.1})
    stuck = threading.Event()
    mac = MacNotifications(quick, queue_size=1, runner=lambda args, **kwargs: stuck.wait(2))
    mac.show('Cup', 'one')                      # taken by the thread, which is stuck on it
    time.sleep(0.05)
    mac.show('Cup', 'two')                      # fills the queue
    started = time.monotonic()
    mac.close()
    assert time.monotonic() - started < 1
    stuck.set()
