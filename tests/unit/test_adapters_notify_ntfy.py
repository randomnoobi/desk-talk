"""NtfyNotifications: warnings, reminders and sips sent to your phone through an ntfy server, on one background thread
with a time limit for the whole send, and a pause after a failure. Nothing here touches the network: a fake `post`
stands in for the real one, and a FakeClock moves the pause along."""

from __future__ import annotations

import email.message
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import urllib.response

import pytest

from desktalk.adapters.notify.ntfy import NtfyNotifications, post_json
from desktalk.config import NotificationsSettings
from desktalk.ports import DesktopNotifications
from tests.fakes import FakeClock

QUEUE = 8
TOPIC = 'desktalk-a-very-secret-name'
RETRY_AFTER = 60.0
SETTINGS = NotificationsSettings.model_construct(phone_server='https://ntfy.example', phone_time_limit_sec=7.0,
                                                 phone_retry_after_sec=RETRY_AFTER)
QUICK = SETTINGS.model_copy(update={'phone_time_limit_sec': 0.05})     # a send given up on after 0.05 s
WAIT = 2.0                                                              # the most a test waits for the thread


class Post:
    """Stands in for post_json: remembers each call, can be held up or fail (`errors`: one per call, None = it
    worked). `hold`: each call waits for it to be set (`started` is set once a call has begun)."""

    def __init__(self, errors=(), hold: threading.Event | None = None):
        self.calls: list[tuple[str, dict, float]] = []
        self.errors, self.hold = list(errors), hold
        self.threads: set[str] = set()
        self.started = threading.Event()

    def __call__(self, url, body, time_limit):
        self.threads.add(threading.current_thread().name)
        self.calls.append((url, json.loads(body.decode('utf-8')), time_limit))
        self.started.set()
        if self.hold is not None:
            self.hold.wait(WAIT)
        error = self.errors.pop(0) if self.errors else None
        if error:
            raise error


def phone_for(post, settings=SETTINGS, clock=None, queue_size=QUEUE):
    return NtfyNotifications(settings, TOPIC, queue_size=queue_size, clock=clock or FakeClock(), post=post)


def said(post, *messages, clock=None, settings=SETTINGS):
    """Deal with each (title, text) in turn, the way the background thread does, and return the sender."""
    phone = phone_for(post, settings, clock)
    for title, text in messages:
        phone._say(title, text)
    phone.close()
    return phone


def wait_until(check):
    deadline = time.monotonic() + WAIT
    while not check() and time.monotonic() < deadline:
        time.sleep(0.005)
    return check()


def test_it_is_desktop_notifications():
    phone = phone_for(Post())
    assert isinstance(phone, DesktopNotifications)
    phone.close()


def test_it_posts_the_topic_title_and_words_as_json_to_the_server():
    post = Post()
    said(post, ('Cup', 'Time for some water? 💧'), ('Lens', 'The camera is covered'))
    assert post.calls == [
        ('https://ntfy.example/', {'topic': TOPIC, 'title': 'Cup', 'message': 'Time for some water? 💧'}, 7.0),
        ('https://ntfy.example/', {'topic': TOPIC, 'title': 'Lens', 'message': 'The camera is covered'}, 7.0)]


def test_a_title_that_is_not_plain_english_letters_still_goes():
    post = Post()
    said(post, ('Tée ☕', 'Ünïcode'))
    assert post.calls[0][1]['title'] == 'Tée ☕'


def test_show_never_waits_for_the_server_and_the_background_thread_sends_it():
    hold = threading.Event()
    post = Post(hold=hold)
    phone = phone_for(post)
    started = time.monotonic()
    phone.show('Cup', 'Time for water')
    assert time.monotonic() - started < 0.2
    assert post.started.wait(WAIT)
    hold.set()
    phone.close()
    assert len(post.calls) == 1 and threading.current_thread().name not in post.threads


def test_after_a_failure_nothing_is_tried_until_the_pause_is_over():
    """With no internet, the ones in the pause are dropped at once, with no call: the queue never backs up."""
    clock = FakeClock()
    no_internet = urllib.error.URLError('nodename nor servname provided')
    post = Post(errors=[no_internet, no_internet])
    phone = phone_for(post, clock=clock)
    phone._say('Cup', 'one')                        # fails: the pause starts
    clock.advance(RETRY_AFTER - 1)
    phone._say('Cup', 'two')                        # in the pause: dropped, not tried
    assert [c[1]['message'] for c in post.calls] == ['one']
    clock.advance(1)
    phone._say('Cup', 'three')                      # the pause is over: tried, and fails again
    clock.advance(RETRY_AFTER - 1)
    phone._say('Cup', 'four')                       # ...so a new pause
    clock.advance(1)
    phone._say('Cup', 'five')                       # tried, and it works
    phone._say('Cup', 'six')                        # ...so the next one goes at once
    phone.close()
    assert [c[1]['message'] for c in post.calls] == ['one', 'three', 'five', 'six']


def test_a_failure_is_only_logged_once_until_it_works_again(caplog):
    clock = FakeClock()
    no_internet = urllib.error.URLError('nodename nor servname provided')
    post = Post(errors=[no_internet, TimeoutError('timed out'), None, no_internet])
    phone = phone_for(post, clock=clock)
    with caplog.at_level(logging.INFO, logger='desktalk.adapters.notify.ntfy'):
        for n in range(4):
            phone._say('Cup', f'message {n}')
            if n != 2:
                phone._say('Cup', 'in the pause')   # dropped, and not logged
            clock.advance(RETRY_AFTER)
    phone.close()
    assert len(post.calls) == 4
    problems = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(problems) == 2                                       # the first failure, and the first after it worked
    assert 'https://ntfy.example' in problems[0].getMessage()
    assert sum('reach your phone again' in r.getMessage() for r in caplog.records) == 1


def test_one_send_is_given_up_on_after_the_time_limit_whatever_it_waits_for(caplog):
    """urlopen's own time limit is for each step, and looking up the server's name has none: the whole send has
    one, and one that runs over counts as a failure."""
    hold = threading.Event()
    post = Post(hold=hold)
    clock = FakeClock()
    phone = phone_for(post, settings=QUICK, clock=clock)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger='desktalk.adapters.notify.ntfy'):
        phone._say('Cup', 'stuck')
    assert time.monotonic() - started < 1.0
    assert 'TimeoutError' in caplog.text
    phone._say('Cup', 'in the pause')               # it counted as a failure, so this is not tried
    hold.set()
    phone.close()
    assert [c[1]['message'] for c in post.calls] == ['stuck']


def test_the_topic_never_reaches_the_log(caplog):
    clock = FakeClock()
    leaky = urllib.error.HTTPError(f'https://ntfy.example/{TOPIC}', 429, f'too many for {TOPIC}', None, None)  # type: ignore[arg-type]
    phone = phone_for(Post(errors=[leaky, None]), clock=clock)
    with caplog.at_level(logging.DEBUG):
        phone._say('Cup', 'one')
        phone._say('Cup', 'two')
        clock.advance(RETRY_AFTER)
        phone._say('Cup', 'three')
    phone.close()
    assert caplog.records and TOPIC not in caplog.text


def test_a_full_queue_drops_the_new_one_and_says_so(caplog):
    hold = threading.Event()
    phone = phone_for(Post(hold=hold), queue_size=1)
    with caplog.at_level(logging.WARNING):
        for n in range(4):
            phone.show('Cup', f'message {n}')
    hold.set()
    phone.close()
    assert 'dropped' in caplog.text


def test_close_drops_what_is_waiting_and_never_waits_for_a_send_under_way():
    """Desk Talk's stop must not wait for the phone: what is waiting is dropped, and a send under way is left to end
    by itself."""
    hold = threading.Event()
    post = Post(hold=hold)
    phone = phone_for(post)
    for n in range(3):
        phone.show('Cup', f'message {n}')
    assert post.started.wait(WAIT)
    started = time.monotonic()
    phone.close()
    assert time.monotonic() - started < 0.2
    hold.set()
    assert wait_until(lambda: not phone._thread.is_alive())
    assert [c[1]['message'] for c in post.calls] == ['message 0']


def test_close_with_nothing_to_send_ends_the_background_thread():
    phone = phone_for(Post())
    phone.close()
    assert wait_until(lambda: not phone._thread.is_alive())


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


class Redirecting(urllib.request.HTTPHandler):
    """Stands in for the network: the first answer is a redirect, any other a plain 200. Remembers each request."""

    def __init__(self):
        super().__init__()
        self.requests: list[tuple[str, str]] = []

    def http_open(self, req):
        self.requests.append((req.get_method(), req.full_url))
        headers = email.message.Message()
        code = 302 if len(self.requests) == 1 else 200
        if code == 302:
            headers['Location'] = 'http://elsewhere.example/'
        answer = urllib.response.addinfourl(io.BytesIO(b''), headers, req.full_url, code=code)
        answer.msg = 'Found' if code == 302 else 'OK'           # type: ignore[attr-defined]
        return answer


def test_post_json_never_follows_a_redirect(monkeypatch):
    """A redirected POST would be sent again as a GET, or somewhere else, and look like it worked: it's a failure."""
    network = Redirecting()
    real = urllib.request.build_opener
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: real(*handlers, network))
    with pytest.raises(urllib.error.HTTPError) as refused:
        post_json('http://ntfy.example/', b'{}', 3.0)
    assert refused.value.code == 302
    assert network.requests == [('POST', 'http://ntfy.example/')]
