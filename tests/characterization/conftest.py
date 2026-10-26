"""
Fixtures for the characterization tests: ready-made desks, each with its two web servers.

    desk = desks('busy')    # see cases.py for the list of desks
    desk.main               # WebClient for the main server (web_app/ and the Pi)
    desk.phone              # WebClient for the phone camera's own server

Each desk is the whole app built by bootstrap.build(), in temp folders, with a fake detector, a fake AI, a fake
test runner for the developer page (DEV_TESTS) and a FakeClock; nothing touches data/, .env, a camera, the
network or a Pi, and no test runs. The settings come only from the config
files in git (not your settings.local.yaml), the danger-zone rules from the copy next to this file, and the
clock reads 10:00 on 21 Oct 2026 (local time) when the requests are sent, so "today" is the same on every run.

client.py (how requests are sent) and this file (how a desk is built) are the only places that know about
the web framework and how the app is put together.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from desktalk.bootstrap import DeskTalk, build
from desktalk.domain import BBox, Detection, TestItem
from desktalk.web.pairing import Links
from tests.conftest import FakeAI
from tests.fakes import FakeClock, FakeDesktopNotifications, FakeDetector, FakeTestRunner
from tests.integration.app_kit import changed, desk_config, inbox

from .cases import DEV_TEST_ID, PAIR_KEY, PHONE_HEADERS, PHONE_URL, PI_HEADERS, PICTURE
from .client import WebClient

HERE = Path(__file__).resolve().parent
RULES_COPY = HERE / 'safety_rules.json'     # config/safety_rules.json as committed (the app rewrites the real one)
PINNED_NOW = (2026, 10, 21, 10, 0, 0)       # local time when the requests are sent
WATCH_SEC = 6                               # how long the camera watches the busy desk
WATCH_STARTS_AGO_SEC = 60
COMPUTER_ADDRESS = '192.168.1.20'           # this computer on the Wi-Fi (in the Pi command and the phone link)


def det(label: str, x1: float, y1: float, x2: float, y2: float, conf: float = 0.9) -> Detection:
    return Detection(label, conf, BBox(x1, y1, x2, y2))


# What the camera "sees" on a busy desk (plus a person, added by Desk.watch).
ON_THE_DESK = [det('laptop', 200, 200, 400, 350), det('cup', 410, 250, 450, 320), det('key', 50, 50, 80, 70),
               det('book', 470, 380, 600, 460), det('remote', 560, 20, 620, 60, conf=0.3)]
PERSON = det('person', 400, 0, 640, 300, conf=0.95)
# The tests the developer page's (fake) test runner finds.
DEV_TESTS = [TestItem(DEV_TEST_ID, DEV_TEST_ID.split('::')[0], ()),
             TestItem('tests/unit/test_example.py::test_slow', 'tests/unit/test_example.py', ('slow',))]
# What the fake vision AI says about the picture (the lost-items scan asks it).
SCENE = '{"visible": ["keys"]}'


def fake_ai(request: dict) -> str:
    """The fake AI: answers in character, logs water or a to-do when asked, and describes pictures."""
    last = request['messages'][-1]['content']
    if isinstance(last, list):                     # text + picture: a vision scan
        return SCENE
    text = last.lower()
    if 'water' in text:
        return 'Splash, nice one! [LOG_SIP]'
    if 'milk' in text:
        return 'On the list it goes. [ADD_TODO:Buy milk]'
    return 'Hey! All good over here on the desk.'


class Desk:
    def __init__(self, app: DeskTalk, clock: FakeClock, detector: FakeDetector, links: Links):
        self.app = app
        self.clock = clock
        self.detector = detector
        self.main = WebClient.main_server(app, links)
        self.phone = WebClient.phone_server(app)

    def server(self, name: str) -> WebClient:
        return {'main': self.main, 'phone': self.phone}[name]

    def watch(self, seconds: float, objects=()) -> None:
        """The camera takes pictures (camera.fps a second) of `objects` and a person, and the app looks at
        each one as it arrives."""
        step = 1 / self.app.config.camera.fps
        for _ in range(max(1, round(seconds / step))):
            self.detector.detections = [*objects, PERSON]
            assert inbox(self.app).push(PICTURE)
            self.app.producer.tick(self.clock.now())
            self.clock.advance(step)


@pytest.fixture
def desks(tmp_path):
    """desks(name) builds one of the desks listed in cases.py. One desk per test (they share the temp folder)."""
    built: dict[str, Desk] = {}
    pinned = time.mktime(PINNED_NOW + (0, 0, -1))

    def make(name: str) -> Desk:
        if name in built:
            return built[name]
        assert not built, f'one desk per test: already built {list(built)}, asked for {name}'
        if name not in ('fresh', 'busy', 'no_ai', 'ai_down', 'phone'):
            raise ValueError(f'no desk called {name!r}')
        reply: Any = {'no_ai': None, 'ai_down': ConnectionError('Connection refused')}.get(name, fake_ai)
        # ollama also gives the busy desk a (fake) vision AI, for the lost-items scan of the pictures
        config = desk_config(tmp_path, ai=reply is not None, ollama=name == 'busy')
        shutil.copy(RULES_COPY, config.config_folder / 'safety_rules.json')
        if name == 'phone':
            config = changed(config, 'camera', source='phone')
        clock = FakeClock(pinned - WATCH_STARTS_AGO_SEC)
        detector = FakeDetector()
        app = build(config, clock=clock, detector=detector, data_folder=tmp_path / 'run', pair_key=PAIR_KEY,
                    synchronous_events=True, set_up_logging=False,
                    test_runner=FakeTestRunner(DEV_TESTS, clock=clock), desktop=FakeDesktopNotifications(),
                    client_factory=None if reply is None else lambda **kw: FakeAI(reply=reply, **kw))
        phone_port = config.camera.phone_port if name == 'phone' else None    # its server runs only then
        links = Links(COMPUTER_ADDRESS, config.web.port, app.pair_key, phone_port=phone_port)
        desk = Desk(app, clock, detector, links)
        if name in ('busy', 'no_ai', 'ai_down'):
            desk.watch(WATCH_SEC, ON_THE_DESK)
        clock.advance(pinned - clock.now())
        if name == 'busy':
            assert desk.main.post('/api/pi/frame', data=PICTURE, headers=PI_HEADERS).status == 200
        if name == 'phone':
            assert links.phone_url == PHONE_URL
            assert desk.phone.post('/api/phone/frame', data=PICTURE, headers=PHONE_HEADERS).status == 200
        built[name] = desk
        return desk

    return make
