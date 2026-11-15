"""
Warnings, reminders and sips sent to your phone as push notifications, through an ntfy server (https://ntfy.sh, or
your own: settings → notifications.phone_server). You subscribe to your topic in the ntfy app on the phone.

The topic is a secret (NTFY_TOPIC in .env): anyone who knows it can read and send these, so it is never logged and
never goes in the address; it goes in the JSON body (ntfy's "publish as JSON", which also lets a title have any
letters, not only plain English ones).

show() never waits (queued.py): one background thread sends each in turn, with a time limit (settings →
notifications.phone_time_limit_sec). Each one is tried once and never again, so with no internet nothing piles up or
goes on forever. A failure is only logged: the first one, and then a line when it works again.

    phone = NtfyNotifications(config.notifications, topic, queue_size=config.notifications.queue_size)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable

from desktalk.adapters.notify.queued import QueuedNotifications
from desktalk.config import NotificationsSettings

log = logging.getLogger(__name__)

Post = Callable[[str, bytes, float], None]      # (the address, the JSON body, the time limit)
HIDDEN = '<topic>'                              # what the topic is swapped for in a logged error


def post_json(url: str, body: bytes, time_limit: float) -> None:
    """POST `body` as JSON to `url`, straight there (no proxy). Raises when it fails or the server says no."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json'})
    with opener.open(request, timeout=time_limit) as answer:
        answer.read()


class NtfyNotifications(QueuedNotifications):
    """`topic`: the secret topic your phone subscribes to. `post` is post_json when not given (a test gives its own,
    so nothing is really sent; tests/conftest.py makes sure of it). `queue_size`: how many can wait for the
    background thread before new ones are dropped."""

    def __init__(self, settings: NotificationsSettings, topic: str, *, queue_size: int, post: Post | None = None):
        self._server = settings.phone_server
        self._topic = topic
        self._post = post if post is not None else post_json
        self._failing = False           # a failure is in the log, until it next works
        super().__init__(queue_size=queue_size, time_limit_sec=settings.phone_time_limit_sec)

    def _say(self, title: str, text: str) -> None:
        body = json.dumps({'topic': self._topic, 'title': title, 'message': text}).encode('utf-8')
        try:
            self._post(f'{self._server}/', body, self._time_limit)
        except Exception as error:      # noqa: BLE001  (any failure is only logged: the chat must go on)
            if not self._failing:
                log.warning('Could not send a notification to your phone through %s (%s: %s). This is only logged '
                            'once until it works again.', self._server, type(error).__name__,
                            self._without_topic(str(error)))
            self._failing = True
            return
        if self._failing:
            log.info('Notifications reach your phone again (through %s)', self._server)
        self._failing = False
        log.info('Sent a notification to your phone (%s)', title)        # names who, not the words

    def _without_topic(self, text: str) -> str:
        return text.replace(self._topic, HIDDEN) if self._topic else text
