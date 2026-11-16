"""
Warnings, reminders and sips sent to your phone as push notifications, through an ntfy server (https://ntfy.sh, or
your own: settings → notifications.phone_server). You subscribe to your topic in the ntfy app on the phone.

The topic is a secret (NTFY_TOPIC in .env): anyone who knows it can read and send these, so it is never logged and
never goes in the address; it goes in the JSON body (ntfy's "publish as JSON", which also lets a title have any
letters, not only plain English ones).

show() never waits (queued.py): one background thread sends each in turn. One send has one time limit for all of it,
looking up the server's name too (settings → notifications.phone_time_limit_sec): the post runs on a short-lived thread
of its own, and one that runs over counts as a failure and is left to end by itself. Each one is tried once and never
again. After a failure nothing is tried for a while (notifications.phone_retry_after_sec, with desktalk_shared's
Breaker): the ones in that time are dropped at once, with no call, so with no Wi-Fi the queue never backs up. A failure
is only logged: the first one, and then a line when it works again. A redirect counts as a failure too, so a POST is
never sent again as a GET, or somewhere else. close() drops whatever is waiting and doesn't wait for a send under way,
so Desk Talk's stop never waits for the phone.

    phone = NtfyNotifications(config.notifications, topic, queue_size=config.notifications.queue_size, clock=clock)
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import urllib.request
from collections.abc import Callable

from desktalk.adapters.notify.queued import STOP, QueuedNotifications
from desktalk.config import NotificationsSettings
from desktalk.ports import Clock
from desktalk_shared.backoff import Backoff
from desktalk_shared.breaker import Breaker

log = logging.getLogger(__name__)

Post = Callable[[str, bytes, float], None]      # (the address, the JSON body, the time limit)
HIDDEN = '<topic>'                              # what the topic is swapped for in a logged error


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is a failure: following it would send the notification again as a GET, or somewhere else."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None                     # no new request: urllib raises an HTTPError with the redirect's code


def post_json(url: str, body: bytes, time_limit: float) -> None:
    """POST `body` as JSON to `url`, straight there (no proxy, no redirects). Raises when it fails or the server says
    no. `time_limit` is urllib's, for each step: NtfyNotifications puts one on the whole of it."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
    request = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json'})
    with opener.open(request, timeout=time_limit) as answer:
        answer.read()


class NtfyNotifications(QueuedNotifications):
    """`topic`: the secret topic your phone subscribes to. `post` is post_json when not given (a test gives its own,
    so nothing is really sent; tests/conftest.py makes sure of it). `queue_size`: how many can wait for the
    background thread before new ones are dropped. `clock`: for the pause after a failure."""

    def __init__(self, settings: NotificationsSettings, topic: str, *, queue_size: int, clock: Clock,
                 post: Post | None = None):
        self._server = settings.phone_server
        self._topic = topic
        self._post = post if post is not None else post_json
        self._clock = clock
        self._retry_after = settings.phone_retry_after_sec
        self._breaker = Breaker(1, Backoff(self._retry_after, self._retry_after))   # one failure starts the pause
        self._failing = False           # a failure is in the log, until it next works
        super().__init__(queue_size=queue_size, time_limit_sec=settings.phone_time_limit_sec)

    def close(self) -> None:
        """Drop whatever is waiting and stop the background thread, without waiting for a send under way."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with contextlib.suppress(queue.Full):   # a show() just filled it again: the thread is a daemon, so that's fine
            self._queue.put_nowait(STOP)

    def _say(self, title: str, text: str) -> None:
        if not self._breaker.let_in(self._clock.monotonic()):
            log.debug('Not sent to your phone (%s): a send failed less than %g seconds ago', title, self._retry_after)
            return
        body = json.dumps({'topic': self._topic, 'title': title, 'message': text}).encode('utf-8')
        try:
            self._send(body)
        except Exception as error:      # noqa: BLE001  (any failure is only logged: the chat must go on)
            self._breaker.failed(self._clock.monotonic())
            if not self._failing:
                log.warning('Could not send a notification to your phone through %s (%s: %s). Nothing more is tried '
                            'for %g seconds, and this is only logged once until it works again.', self._server,
                            type(error).__name__, self._without_topic(str(error)), self._retry_after)
            self._failing = True
            return
        self._breaker.worked()
        if self._failing:
            log.info('Notifications reach your phone again (through %s)', self._server)
        self._failing = False
        log.info('Sent a notification to your phone (%s)', title)        # names who, not the words

    def _send(self, body: bytes) -> None:
        """Post it, giving up after the time limit for the whole of it. One given up on is left to end by itself."""
        outcome: list[Exception | None] = []

        def post() -> None:
            try:
                self._post(f'{self._server}/', body, self._time_limit)
            except Exception as error:  # noqa: BLE001  (handed to the background thread, which logs it)
                outcome.append(error)
            else:
                outcome.append(None)

        sender = threading.Thread(target=post, name='phone notification', daemon=True)
        sender.start()
        sender.join(self._time_limit)
        if not outcome:
            raise TimeoutError(f'no answer within {self._time_limit:g} seconds')
        if outcome[0] is not None:
            raise outcome[0]

    def _without_topic(self, text: str) -> str:
        return text.replace(self._topic, HIDDEN) if self._topic else text
