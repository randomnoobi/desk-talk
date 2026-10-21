"""
Fixtures for the characterization tests: ready-made desks, each with its two web servers.

    desk = desks('busy')    # see cases.py for the list of desks
    desk.main               # WebClient for the main server (web_app/ and the Pi)
    desk.phone              # WebClient for the phone camera's own server

Everything runs in temp folders with a fake detector and a fake AI; nothing touches data/, .env, a camera,
the network or a Pi. The settings come only from the config files in git (not your settings.local.yaml),
the danger-zone rules from the copy next to this file, and the clock is pinned to 10:00 on 21 Oct 2026
(local time) so "today" is the same on every run.

When the app moves to FastAPI (Phase 3), this file (how a desk is built) and client.py (how requests are
sent) are the only places that should need changes.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from conftest import Feeder, obj

from desktalk.chat.vision_habits import HabitSceneVlmThread
from desktalk.settings import load_settings
from desktalk.vision.pairing import qr_svg

from .cases import PAIR_KEY, PHONE_HEADERS, PHONE_URL, PI_HEADERS, PICTURE
from .client import WebClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONFIG_FILES_IN_GIT = ('settings.yaml', 'protocols.yaml', 'agents.yaml')
RULES_COPY = HERE / 'safety_rules.json'     # config/safety_rules.json as committed (the app rewrites the real one)
PINNED_NOW = (2026, 10, 21, 10, 0, 0)       # local time every test starts at
WATCH_SEC = 6                               # how long the camera watches the busy desk
WATCH_STARTS_AGO_SEC = 60

# What the camera "sees" on a busy desk (plus a person, added by Feeder).
ON_THE_DESK = [obj('laptop', 200, 200, 400, 350), obj('cup', 410, 250, 450, 320), obj('key', 50, 50, 80, 70),
               obj('book', 470, 380, 600, 460), obj('remote', 560, 20, 620, 60, conf=0.3)]
# What the fake vision AI says about the picture (HabitSceneVlmThread asks it).
SCENE = '{"posture": "good", "nail_biting": true, "phone": "none", "attention": "screen"}'


def fake_ai(request: dict) -> str:
    """The fake AI: answers in character, logs water or a to-do when asked, and describes pictures."""
    last = request['messages'][-1]['content']
    if isinstance(last, list):                     # text + picture: a vision check
        return SCENE
    text = last.lower()
    if 'water' in text:
        return 'Splash, nice one! [LOG_SIP]'
    if 'milk' in text:
        return 'On the list it goes. [ADD_TODO:Buy milk]'
    return 'Hey! All good over here on the desk.'


@pytest.fixture
def settings(tmp_path_factory):
    """Settings from the config files in git only, so a local config/settings.local.yaml can't change replies."""
    folder = tmp_path_factory.mktemp('config')
    for name in CONFIG_FILES_IN_GIT:
        shutil.copy(ROOT / 'config' / name, folder / name)
    s = load_settings(config_dir=folder, environ={}, dotenv=Path('/nonexistent'))
    s.llm['min_interval_sec'] = 0          # no waiting between fake AI calls
    return s


@pytest.fixture
def clock(monkeypatch):
    """time.time() starts at PINNED_NOW and then runs at normal speed."""
    start = time.mktime(PINNED_NOW + (0, 0, -1))
    t0 = time.monotonic()

    def now() -> float:
        return start + (time.monotonic() - t0)

    monkeypatch.setattr(time, 'time', now)
    return now


class Desk:
    def __init__(self, app, clock):
        self.app = app
        self.clock = clock
        self.main = WebClient.main_server(app)
        self.phone = WebClient.phone_server(app)

    def server(self, name: str) -> WebClient:
        return {'main': self.main, 'phone': self.phone}[name]

    def watch(self, seconds: float, objects=(), started_ago: float = 0.0) -> None:
        """The camera takes pictures (3 a second) of `objects` and a person, and the app looks at them."""
        Feeder(self.app, start=self.clock() - started_ago).run(seconds, list(objects))


@pytest.fixture
def desks(make_app, settings, clock, tmp_path):
    """desks(name) builds one of the desks listed in cases.py. One desk per test (they share the temp folder)."""
    built: dict[str, Desk] = {}

    def build(name: str) -> Desk:
        if name in built:
            return built[name]
        assert not built, f'one desk per test: already built {list(built)}, asked for {name}'
        shutil.copy(RULES_COPY, tmp_path / 'safety_rules.json')    # make_app uses this file if it's there
        if name == 'fresh':
            app = make_app(ai_reply=fake_ai, secrets={'gemini_api_key': 'fake'})
        elif name == 'busy':
            # ollama_url also gives the app a (fake) vision AI, for the habit checks on pictures
            app = make_app(ai_reply=fake_ai, secrets={'gemini_api_key': 'fake', 'ollama_url': 'http://ollama.test'})
        elif name == 'no_ai':
            app = make_app()
        elif name == 'ai_down':
            app = make_app(ai_reply=ConnectionError('Connection refused'), secrets={'gemini_api_key': 'fake'})
        elif name == 'phone':
            settings.camera['source'] = 'phone'
            app = make_app(ai_reply=fake_ai, secrets={'gemini_api_key': 'fake'})
            app.phone_url, app.phone_qr_svg = PHONE_URL, qr_svg(PHONE_URL)    # what start_phone_server() sets
        else:
            raise ValueError(f'no desk called {name!r}')
        app.habits.clock = clock          # its default clock was bound before the clock was pinned
        assert app.pair_key == PAIR_KEY
        desk = Desk(app, clock)
        if name in ('busy', 'no_ai', 'ai_down'):
            desk.watch(WATCH_SEC, ON_THE_DESK, started_ago=WATCH_STARTS_AGO_SEC)
        if name == 'busy':
            assert desk.main.post('/api/pi/frame', data=PICTURE, headers=PI_HEADERS).status == 200
            HabitSceneVlmThread(app).tick()        # one round of the vision habit check
        if name == 'phone':
            assert desk.phone.post('/api/phone/frame', data=PICTURE, headers=PHONE_HEADERS).status == 200
        built[name] = desk
        return desk

    return build
