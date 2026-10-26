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

from desktalk.adapters.notify import mac


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
