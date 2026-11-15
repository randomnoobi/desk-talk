"""
Shared test helpers.

    uv run pytest                     # everything
    uv run pytest tests/unit          # just the fast unit tests
    uv run pytest -m "not slow"       # skip the real-YOLO tests

Tests never touch your real data/ folder, .env or camera: the whole-app tests build the app in a
temporary folder with the fakes in tests/fakes/ and, when needed, the FakeAI below.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from desktalk.adapters.notify import mac, ntfy, speaker


@pytest.fixture(autouse=True)
def osascript_calls(monkeypatch):
    """No test ever shows a real Mac pop-up: a MacNotifications made without its own runner (as build() makes one
    from the settings in git) gets this recorder instead of the real osascript, and a test that reached it fails.
    A test that means to reach it takes this fixture and empties the list."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(mac, 'subprocess', SimpleNamespace(run=lambda *args, **kw: calls.append((args, kw))))
    yield calls
    assert not calls, (f'a test tried to show {len(calls)} real Mac pop-up(s): pass build() a fake `desktop=` '
                       '(tests.fakes.FakeDesktopNotifications)')


@pytest.fixture(autouse=True)
def speak_calls(monkeypatch):
    """No test ever really speaks: a SpeakerNotifications made without its own command gets this recorder instead of
    the real one, and a test that reached it fails. A test that means to reach it takes this fixture and empties the
    list. (A test of the real runner imports run_speak_command by name, which this doesn't change.)"""
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(speaker, 'run_speak_command', lambda args, text, limit, **_: calls.append((args, text, limit)))
    yield calls
    assert not calls, (f'a test tried to really say {len(calls)} thing(s): pass build() a fake `desktop=` '
                       '(tests.fakes.FakeDesktopNotifications)')



class NtfyGuard:
    """What ntfy_guard gives a test: allow(post) lets an NtfyNotifications made without its own `post` (as build()
    makes one when NTFY_TOPIC is set) use that one instead of the real post_json."""

    def __init__(self):
        self.post: ntfy.Post | None = None

    def allow(self, post: ntfy.Post) -> None:
        self.post = post


@pytest.fixture(autouse=True)
def ntfy_guard(monkeypatch):
    """No test ever sends a real phone notification: making an NtfyNotifications without its own `post` fails at
    once, in the test's own thread (the posts themselves happen on a background thread, where a failure would be
    missed). A test that builds one through build(), which can't be given a post, takes this fixture and calls
    allow() with a fake first. (A test of the real post imports post_json by name, which this doesn't change.)"""
    guard = NtfyGuard()
    real_init = ntfy.NtfyNotifications.__init__

    def guarded(self, *args, **kwargs):
        if kwargs.get('post') is None:
            if guard.post is None:
                raise AssertionError('a test tried to make a real phone sender: give NtfyNotifications its own '
                                     '`post=`, pass build() a fake `phone_notifications=` '
                                     '(tests.fakes.FakeDesktopNotifications), or take the ntfy_guard fixture and '
                                     'call allow() with a fake post')
            kwargs['post'] = guard.post
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(ntfy.NtfyNotifications, '__init__', guarded)
    return guard


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class FakeAI:
    """Stands in for the OpenAI client. `reply` is a string or a function of the request."""
    instances = []

    def __init__(self, api_key=None, base_url=None, reply: Any = 'Hi there, nice desk you have!'):
        self.api_key, self.base_url, self.reply = api_key, base_url, reply
        self.requests = []
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.requests.append(kw)
                r = outer.reply(kw) if callable(outer.reply) else outer.reply
                if isinstance(r, Exception):
                    raise r
                return _Resp(r)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()
        FakeAI.instances.append(self)


def blank_frame(w=640, h=480, value=140):
    """A plain grey picture, with some texture so Lens doesn't call it blurry."""
    f = np.full((h, w, 3), value, np.uint8)
    f[::8] = 40
    return f
