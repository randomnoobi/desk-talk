"""NtfyNotifications: warnings, reminders and sips sent to your phone through an ntfy server, on one background thread
with a time limit. Nothing here touches the network: a fake `post` stands in for the real one."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

import pytest

from desktalk.adapters.notify.ntfy import NtfyNotifications, post_json
from desktalk.config import NotificationsSettings
from desktalk.ports import DesktopNotifications

QUEUE = 8
TOPIC = 'desktalk-a-very-secret-name'
SETTINGS = NotificationsSettings.model_construct(phone_server='https://ntfy.example', phone_time_limit_sec=7.0)


class Post:
    """Stands in for post_json: remembers each call, can be slow or fail (`errors`: one per call, None = it worked)."""

    def __init__(self, delay=0.0, errors=()):
        self.calls: list[tuple[str, dict, float]] = []
        self.delay, self.errors = delay, list(errors)
        self.threads: set[str] = set()

    def __call__(self, url, body, time_limit):
        self.threads.add(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((url, json.loads(body.decode('utf-8')), time_limit))
        error = self.errors.pop(0) if self.errors else None
        if error:
            raise error


def sent(post, *messages):
    """Send each (title, text) and wait until all of them have been dealt with."""
    phone = NtfyNotifications(SETTINGS, TOPIC, queue_size=QUEUE, post=post)
    for title, text in messages:
        phone.show(title, text)
    phone.close()
    return phone


def test_it_is_desktop_notifications():
    phone = NtfyNotifications(SETTINGS, TOPIC, queue_size=QUEUE, post=Post())
    assert isinstance(phone, DesktopNotifications)
    phone.close()


def test_it_posts_the_topic_title_and_words_as_json_to_the_server():
    post = Post()
    sent(post, ('Cup', 'Time for some water? 💧'), ('Lens', 'The camera is covered'))
    assert post.calls == [
        ('https://ntfy.example/', {'topic': TOPIC, 'title': 'Cup', 'message': 'Time for some water? 💧'}, 7.0),
        ('https://ntfy.example/', {'topic': TOPIC, 'title': 'Lens', 'message': 'The camera is covered'}, 7.0)]


def test_a_title_that_is_not_plain_english_letters_still_goes():
    post = Post()
    sent(post, ('Tée ☕', 'Ünïcode'))
    assert post.calls[0][1]['title'] == 'Tée ☕'


def test_show_never_waits_for_the_server():
    post = Post(delay=0.5)
    phone = NtfyNotifications(SETTINGS, TOPIC, queue_size=QUEUE, post=post)
    started = time.monotonic()
    phone.show('Cup', 'Time for water')
    assert time.monotonic() - started < 0.2
    phone.close()
    assert post.calls and threading.current_thread().name not in post.threads


def test_a_failure_is_only_logged_once_until_it_works_again(caplog):
    no_internet = urllib.error.URLError('nodename nor servname provided')
    post = Post(errors=[no_internet, no_internet, TimeoutError('timed out'), None, no_internet])
    with caplog.at_level(logging.INFO, logger='desktalk.adapters.notify.ntfy'):
        sent(post, *[('Cup', f'message {n}') for n in range(5)])
    assert len(post.calls) == 5                                     # each one tried once, none again
    problems = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(problems) == 2                                       # the first failure, and the first after it worked
    assert 'https://ntfy.example' in problems[0].getMessage()
    assert sum('reach your phone again' in r.getMessage() for r in caplog.records) == 1


def test_the_topic_never_reaches_the_log(caplog):
    leaky = urllib.error.HTTPError(f'https://ntfy.example/{TOPIC}', 429, f'too many for {TOPIC}', None, None)  # type: ignore[arg-type]
    with caplog.at_level(logging.DEBUG):
        sent(Post(errors=[leaky, None]), ('Cup', 'one'), ('Cup', 'two'))
    assert caplog.records and TOPIC not in caplog.text


def test_a_full_queue_drops_the_new_one_and_says_so(caplog):
    release = threading.Event()

    def stuck(url, body, time_limit):
        release.wait(2)

    phone = NtfyNotifications(SETTINGS, TOPIC, queue_size=1, post=stuck)
    with caplog.at_level(logging.WARNING):
        for n in range(4):
            phone.show('Cup', f'message {n}')
    release.set()
    phone.close()
    assert 'dropped' in caplog.text


class Answer:
    def __init__(self):
        self.read_called = False

    def read(self):
        self.read_called = True
        return b'{}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_post_json_posts_json_with_the_time_limit_and_no_proxy(monkeypatch):
    seen: dict = {}
    answer = Answer()

    class Opener:
        def open(self, request, timeout):
            seen.update(request=request, timeout=timeout)
            return answer

    def build_opener(*handlers):
        seen['handlers'] = handlers
        return Opener()

    monkeypatch.setattr(urllib.request, 'build_opener', build_opener)
    post_json('https://ntfy.example/', b'{"topic": "t"}', 3.0)
    request = seen['request']
    assert request.get_method() == 'POST' and request.full_url == 'https://ntfy.example/'
    assert request.data == b'{"topic": "t"}' and request.get_header('Content-type') == 'application/json'
    assert seen['timeout'] == 3.0 and answer.read_called
    proxies = [h for h in seen['handlers'] if isinstance(h, urllib.request.ProxyHandler)]
    assert proxies and vars(proxies[0])['proxies'] == {}             # not even the ones in the environment


def test_post_json_raises_when_the_server_says_no(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, 'forbidden', None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: Opener())
    with pytest.raises(urllib.error.HTTPError):
        post_json('https://ntfy.example/', b'{}', 3.0)
