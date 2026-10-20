"""
Shared test helpers.

    python -m pytest                  # everything
    python -m pytest tests/unit       # just the fast unit tests
    python -m pytest -m "not slow"    # skip the real-YOLO tests

Tests never touch your real data/ folder, .env or camera: each one builds
the app in a temporary folder with a fake detector (you script what it
"sees") and, when needed, a fake AI.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desktalk.agents.profiles import ProfileBook  # noqa: E402
from desktalk.events.stream import EventStream  # noqa: E402
from desktalk.habits.tracker import HabitTracker  # noqa: E402
from desktalk.settings import Preferences, load_settings  # noqa: E402
from desktalk.desk_state import DeskState  # noqa: E402


# ── fake parts ───────────────────────────────────────────────────────
def obj(label, x1, y1, x2, y2, conf=0.9):
    """One detection, the way the detector returns it."""
    return {'label': label, 'conf': conf, 'bbox': [x1, y1, x2, y2], 'center': [(x1 + x2) / 2, (y1 + y2) / 2]}


class FakeDetector:
    """Returns whatever the test put in `.objects` (a list, or a function of the frame)."""
    label = 'fake detector'

    def __init__(self, objects=None):
        self.objects = objects or []
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        return [dict(o) for o in (self.objects(frame) if callable(self.objects) else self.objects)]

    def warmup(self):
        pass


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

    def __init__(self, api_key=None, base_url=None, reply='Hi there, nice desk you have!'):
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
    f = np.full((h, w, 3), value, np.uint8)
    f[::8] = 40                      # texture, so Lens doesn't call it blurry
    return f


def _o(label, conf=0.9):
    return {'label': label, 'conf': conf, 'bbox': [0, 0, 10, 10], 'center': [5, 5]}


class HabitRig:
    """A habit tracker fed by made-up detection events (3 a second)."""

    def __init__(self, settings, tmp_path, phone_counts=True):
        self.stream = EventStream(synchronous=True)
        self.events = []
        self.stream.subscribe('t', self.events.append, types={'habit'})
        self.profiles = ProfileBook(settings.agents_file, learned_path=tmp_path / 'l.json')
        self.prefs = Preferences(settings, tmp_path / 'prefs.json')
        self.t = time.time() - 4 * 3600
        self.tracker = HabitTracker(settings, self.profiles, self.stream, DeskState(), self.prefs,
                                    folder=tmp_path / 'habits', phone_counts=phone_counts, clock=lambda: self.t)

    def run(self, seconds, *labels, person=True, light=None):
        for _ in range(int(seconds * 3)):
            objs = [_o(l) for l in labels] + ([_o('person')] if person else [])
            self.tracker.on_detection({'type': 'detection', 'ts': self.t, 'detected_obj': objs, 'person': person,
                                       'light': light or {}})
            self.t += 1 / 3

    def said(self, action):
        return [e for e in self.events if e['action'] == action]



# ── fixtures ─────────────────────────────────────────────────────────
@pytest.fixture
def settings():
    s = load_settings(environ={}, dotenv=Path('/nonexistent'))
    s.llm['min_interval_sec'] = 0          # no waiting between fake AI calls
    return s


@pytest.fixture
def make_app(tmp_path, settings):
    """Build a DeskTalkApp in a temp folder. Keyword args go to DeskTalkApp."""
    from desktalk.app import DeskTalkApp

    def _make(detector=None, synchronous=True, secrets=None, ai_reply=None, **kw):
        rules = tmp_path / 'safety_rules.json'
        if not rules.exists():
            shutil.copy(ROOT / 'config' / 'safety_rules.json', rules)
        s = settings
        if secrets:
            s['secrets'].update(secrets)
        factory = None
        if ai_reply is not None:
            factory = lambda **k: FakeAI(reply=ai_reply, **k)   # noqa: E731
        app = DeskTalkApp(settings=s, detector=detector or FakeDetector(), synchronous=synchronous,
                          data_dir=tmp_path / 'data', rules_path=rules, pair_key='testkey',
                          llm_client_factory=factory, **kw)
        from desktalk.vision.producer import DetectionProducer
        app.producer = DetectionProducer(app.source, app.detector, app.stream, app.desk, app.camera_health,
                                         app.analyzer, app.profiles, fps=3)
        return app
    return _make


class Feeder:
    """Push pictures through the producer with made-up timestamps."""

    def __init__(self, app, start=None, step=1 / 3):
        self.app = app
        self.t = start if start is not None else time.time()
        self.step = step
        self.frame = blank_frame()

    def run(self, seconds, objects=None, person=True, light=None):
        """Feed `seconds` worth of pictures (3 a second) showing `objects`."""
        det = self.app.detector
        n = max(1, int(round(seconds / self.step)))
        events = []
        for _ in range(n):
            objs = list(objects or [])
            if person:
                objs.append(obj('person', 400, 0, 640, 300, 0.95))
            det.objects = objs
            events.append(self.app.producer.process_frame(self.frame, meta=dict(light or {}), now=self.t))
            self.t += self.step
        return events


@pytest.fixture
def feeder():
    return Feeder


def read_jsonl(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
