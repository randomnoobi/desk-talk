"""
The whole app for the whole-app tests (tests/integration/test_app_*.py): built by bootstrap.build() with a fake
detector, a fake AI (a fake OpenAI client inside the real AI connection and its ResilientChatModel), a FakeClock,
and everything it writes in a temp folder. The background jobs are driven by calling tick(), not by threads.

    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP, CUP_FAR)          # the Pi sends 4 seconds of pictures; the producer analyses each
    rig.messages()                         # what the open app page got since the last call
    rig.app.reminders.tick(rig.clock.now())
"""

from __future__ import annotations

import json
import logging
import queue
import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel

from desktalk.bootstrap import DeskTalk, build
from desktalk.config import AppConfig, load_config
from desktalk.domain import BBox, Detection, UploadInfo
from desktalk.runtime.log_setup import HANDLER_NAME
from tests.conftest import FakeAI
from tests.fakes import FakeClock, FakeDetector, InMemoryKeyValueStore

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILES_IN_GIT = ('settings.yaml', 'protocols.yaml', 'agents.yaml', 'prompts.yaml')
SAFETY_RULES = json.loads((ROOT / 'config' / 'safety_rules.json').read_text())     # as committed
PAIR_KEY = 'test-pair-key'
AFTERNOON = time.mktime((2026, 10, 21, 13, 0, 0, 0, 0, -1))     # 1 pm local time, so the day is the same everywhere

LAPTOP = Detection('laptop', 0.9, BBox(200, 200, 400, 350))
CUP_FAR = Detection('cup', 0.9, BBox(560, 380, 600, 450))
CUP_NEAR = Detection('cup', 0.9, BBox(410, 250, 450, 320))      # inside the laptop's spill zone
PERSON = Detection('person', 0.95, BBox(400, 0, 640, 300))
VISION_ANSWER = '{"posture": "good", "face_screen_distance": "ok", "phone": "none", "attention": "screen"}'


def desk_jpeg(value: int = 140) -> bytes:
    picture = np.full((480, 640, 3), value, np.uint8)
    picture[::8] = 40                      # some texture, so Lens doesn't call it blurry
    return cv2.imencode('.jpg', picture, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes()


def chatty_ai(request: Mapping[str, Any]) -> str:
    """The fake AI's answers: a vision answer for a picture, a friendly line for everything else."""
    if isinstance(request['messages'][-1]['content'], list):
        return VISION_ANSWER
    return 'Hey! All good over here on the desk.'


def changed(config: BaseModel, where: str, **values: Any) -> Any:
    """`config` with some settings changed: changed(config, 'protocols.breaks', sit_limit_min=2)."""
    first, _, rest = where.partition('.')
    section = getattr(config, first)
    inner = changed(section, rest, **values) if rest else section.model_copy(update=values)
    return config.model_copy(update={first: inner})


def desk_config(tmp_path: Path, *, ai: bool = False, ollama: bool = False) -> AppConfig:
    """The settings in git (never your config/settings.local.yaml or .env), in a project folder under tmp_path.
    ai: a (fake) Gemini key; ollama: an Ollama address, so pictures go to a vision AI on this computer.
    AI calls aren't spaced out (llm.min_interval_sec 0), so the FakeClock only moves when a test moves it.
    The web servers serve the pages in this repo (web_app/, phone_camera/)."""
    project = tmp_path / 'project'
    (project / 'config').mkdir(parents=True, exist_ok=True)
    for name in CONFIG_FILES_IN_GIT:
        shutil.copy(ROOT / 'config' / name, project / 'config' / name)
    environ = {}
    if ai:
        environ['GEMINI_API_KEY'] = 'fake-key'
    if ollama:
        environ['OLLAMA_URL'] = 'http://localhost:11434/v1'
    config = load_config(root=project, environ=environ, dotenv=project / '.env')
    config = changed(config, 'web', app_folder=ROOT / 'web_app', phone_folder=ROOT / 'phone_camera')  # the real pages
    return changed(config, 'llm', min_interval_sec=0)


class DeskRig:
    """The whole app, driven by hand. ai_reply: what the fake AI says (a string or a function of the request);
    None = no AI key at all. Other keyword arguments go to build()."""

    def __init__(self, tmp_path: Path, *, config: AppConfig | None = None, start: float = AFTERNOON,
                 ai_reply: str | Callable[[Mapping[str, Any]], str] | None = None, ollama: bool = False,
                 **build_kw: Any):
        self.tmp_path = tmp_path
        # The clock keeps this computer's own time zone, so its local hours match the times written above.
        self.clock = FakeClock(start, utc_offset_hours=time.localtime(start).tm_gmtoff / 3600)
        self.config = config or desk_config(tmp_path, ai=ai_reply is not None, ollama=ollama)
        self.detector = FakeDetector()
        self.ai_clients: list[FakeAI] = []

        def client_factory(**kw: Any) -> FakeAI:
            self.ai_clients.append(FakeAI(reply=ai_reply if ai_reply is not None else chatty_ai, **kw))
            return self.ai_clients[-1]

        kw: dict[str, Any] = {'detector': self.detector, 'client_factory': client_factory,
                              'data_folder': tmp_path / 'run', 'pair_key': PAIR_KEY, 'synchronous_events': True,
                              'set_up_logging': False,
                              'config_store': InMemoryKeyValueStore({'safety_rules': SAFETY_RULES})}
        kw.update(build_kw)
        self.app: DeskTalk = build(self.config, clock=self.clock, **kw)
        self.page = self.app.hub.subscribe()          # an open app page
        self._got: list[dict[str, Any]] = []
        self.jpeg = desk_jpeg()

    # ── pictures ──
    def watch(self, seconds: float, *things: Detection, person: bool = True, step: float = 1 / 3,
              info: UploadInfo | None = None) -> None:
        """The camera sends `seconds` of pictures showing `things` (and a person), `step` seconds apart;
        the producer analyses each one."""
        for _ in range(max(1, round(seconds / step))):
            self.detector.detections = [*things, *([PERSON] if person else [])]
            self.picture(info)
            self.clock.advance(step)

    def picture(self, info: UploadInfo | None = None, jpeg: bytes | None = None) -> None:
        """One picture arrives (like a POST to /api/pi/frame) and the producer takes it."""
        assert self.app.source.push(jpeg or self.jpeg, info)
        self.app.producer.tick(self.clock.now())
        self._drain()

    # ── what came out ──
    def _drain(self) -> None:
        while True:
            try:
                self._got.append(self.page.get_nowait())
            except queue.Empty:
                return

    def messages(self) -> list[dict[str, Any]]:
        """Messages the open app page got since the last call."""
        self._drain()
        got, self._got = self._got, []
        return got

    def tick(self, job: Any, after: float = 0.0) -> float:
        """Move the clock, then run one round of a background job."""
        self.clock.advance(after)
        wait = job.tick(self.clock.now())
        self._drain()
        return wait


def restore_logging(level: int) -> None:
    """Undo setup_logging (for tests that let build() set up the log files): its handlers go, the level comes back."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if h.get_name() == HANDLER_NAME]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)
