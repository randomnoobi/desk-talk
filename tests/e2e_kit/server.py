"""
The whole app on a real web server, for the browser tests (tests/e2e/). Built by bootstrap.build() with a
scripted FakeDetector (a bottle in picture 2), a fake AI that always gives the same reply, the settings in git
and everything it writes under tmp_path. No network, no API key, no camera, no Pi.

    with running_app(tmp_path, pictures=desk_pictures(tmp_path)) as url:     # http://127.0.0.1:<free port>
        ...

    with app_server(tmp_path, pictures=desk_pictures(tmp_path)) as server:
        server.show(2)          # a pretend Pi keeps sending picture 2 (the bottle), at the Pi's pace
        server.stop()           # the server goes away, like closing server.py ...
        server.start()          # ... and comes back on the same port, a fresh app on the same data folder

The background jobs run (lifespan on), on the real clock, so the page sees the app as you would.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np

from desktalk.bootstrap import DeskTalk, build
from desktalk.config import AppConfig
from desktalk.domain import BBox, Detection
from desktalk.web.app import create_app
from desktalk.web.serve import ServerThread, server_config
from desktalk_shared.wire import DETECTOR_TURNED_OFF, DetectorState, UploadInfo, encode_upload
from tests.conftest import FakeAI
from tests.fakes import FakeDetector, InMemoryKeyValueStore
from tests.integration.app_kit import PAIR_KEY, SAFETY_RULES, desk_config
from tests.integration.web_kit import THIS_COMPUTER, links_for

log = logging.getLogger(__name__)

CANNED_REPLY = 'Hello from the fake AI! All good over here on the desk.'
BOTTLE = Detection('bottle', 0.9, BBox(420, 260, 470, 380))
BOTTLE_CHAT = 'Cup'                          # config/agents.yaml: a bottle is Cup
BOTTLE_IN_PICTURE_2: Mapping[int, Sequence[Detection]] = {2: [BOTTLE]}
PICTURE_SIZE = (640, 480)
FINGERPRINT_SIZE = (16, 12)                  # pictures are told apart by a tiny copy of each


def desk_pictures(folder: Path, count: int = 2) -> list[Path]:
    """`count` made-up desk pictures (each a different grey, with some texture so Lens doesn't call them
    blurry), saved as desk-1.jpg, desk-2.jpg ... in folder/pictures."""
    out = folder / 'pictures'
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for number in range(1, count + 1):
        picture = np.full((PICTURE_SIZE[1], PICTURE_SIZE[0], 3), 100 + 20 * number, np.uint8)
        picture[::8] = 40
        path = out / f'desk-{number}.jpg'
        cv2.imwrite(str(path), picture)
        paths.append(path)
    return paths


def _fingerprint(picture: Any) -> np.ndarray:
    return cv2.resize(picture, FINGERPRINT_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)


class PictureScript:
    """What the FakeDetector finds: script[n] in picture n (numbered from 1), nothing in the others.
    A picture is matched to the nearest of `pictures`, so a picture that was sent again still counts."""

    def __init__(self, pictures: Sequence[Path], script: Mapping[int, Sequence[Detection]]):
        self._prints = []
        for path in pictures:
            picture = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if picture is None:
                raise ValueError(f'{path} is not a picture')
            self._prints.append(_fingerprint(picture))
        self._script = script

    def number(self, picture: Any) -> int:
        seen = _fingerprint(picture)
        distances = [float(np.abs(seen - p).mean()) if p.shape == seen.shape else float('inf') for p in self._prints]
        return int(np.argmin(distances)) + 1

    def __call__(self, picture: Any) -> list[Detection]:
        return list(self._script.get(self.number(picture), ()))


class AppServer:
    """The app on a real server on 127.0.0.1. start() builds a fresh app (on the same data folder, like
    running server.py again) and serves it; the port picked the first time is kept for later starts."""

    def __init__(self, tmp_path: Path, *, pictures: Sequence[Path],
                 found: Mapping[int, Sequence[Detection]] = BOTTLE_IN_PICTURE_2,
                 change: Callable[[AppConfig], AppConfig] | None = None):
        self.tmp_path = tmp_path
        self.pictures = list(pictures)
        self.config = desk_config(tmp_path, ai=True)
        if change is not None:
            self.config = change(self.config)
        self.detector = FakeDetector(PictureScript(self.pictures, found))
        self.app: DeskTalk | None = None
        self.port = 0
        self._server: ServerThread | None = None
        self._showing: int | None = None
        self._closing = threading.Event()
        self._feeder: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f'http://{THIS_COMPUTER}:{self.port}'

    # ── the server ──
    def start(self) -> None:
        def client_factory(**kw: Any) -> FakeAI:
            return FakeAI(reply=CANNED_REPLY, **kw)

        self.app = build(self.config, detector=self.detector, client_factory=client_factory,
                         data_folder=self.tmp_path / 'run', pair_key=PAIR_KEY, set_up_logging=False,
                         config_store=InMemoryKeyValueStore({'safety_rules': SAFETY_RULES}))
        web = create_app(self.app, links_for(self.app))
        self._server = ServerThread(server_config(web, host=THIS_COMPUTER, port=self.port, lifespan='on'), name='e2e')
        self._server.start()
        self.port = self._server.port

    def stop(self) -> None:
        """Stop the server (and with it the app's background jobs). The pretend Pi keeps trying."""
        if self._server is not None:
            self._server.stop()
            self._server = None

    # ── the pretend Pi ──
    def show(self, number: int | None) -> None:
        """From now on the pretend Pi sends picture `number` (from 1) over and over, at settings → pi.fps,
        the way the camera program does (upload format 2, its own detector turned off, so this computer
        detects). None: it stops sending."""
        if number is not None and not 1 <= number <= len(self.pictures):
            raise ValueError(f'there is no picture {number}')
        self._showing = number
        if number is not None and self._feeder is None:
            self._feeder = threading.Thread(target=self._feed, name='pretend Pi', daemon=True)
            self._feeder.start()

    def _feed(self) -> None:
        jpegs = [path.read_bytes() for path in self.pictures]
        wait = 1 / self.config.pi.fps
        sent = 0
        with httpx.Client(timeout=self.config.pi.upload_timeout_sec) as client:
            while not self._closing.is_set():
                number = self._showing
                if number is not None:
                    sent += 1
                    info = UploadInfo(frame_id=f'pi-{sent:06d}', taken_at=time.time(), detector=DetectorState.OFF,
                                      detector_reason=DETECTOR_TURNED_OFF)
                    body, content_type = encode_upload(jpegs[number - 1], info)
                    try:
                        client.post(f'{self.url}/api/pi/frame', content=body,
                                    headers={'Content-Type': content_type, 'X-Desk-Key': PAIR_KEY})
                    except httpx.HTTPError as exc:          # the server is down for a moment: keep trying
                        log.debug('The pretend Pi could not send a picture: %s', exc)
                self._closing.wait(wait)                    # the Pi's pace; close() ends it at once

    def close(self) -> None:
        self._closing.set()
        if self._feeder is not None:
            self._feeder.join()
            self._feeder = None
        self.stop()


@contextlib.contextmanager
def app_server(tmp_path: Path, *, pictures: Sequence[Path],
               found: Mapping[int, Sequence[Detection]] = BOTTLE_IN_PICTURE_2,
               change: Callable[[AppConfig], AppConfig] | None = None) -> Iterator[AppServer]:
    """A started AppServer, stopped afterwards. `found`: what the detector on this computer finds in each
    picture (by number, from 1); `change`: changes to the settings in git."""
    server = AppServer(tmp_path, pictures=pictures, found=found, change=change)
    server.start()
    try:
        yield server
    finally:
        server.close()


@contextlib.contextmanager
def running_app(tmp_path: Path, *, pictures: list[Path]) -> Iterator[str]:
    """The app on a real server, with the pretend Pi sending picture 1. Yields its address."""
    with app_server(tmp_path, pictures=pictures) as server:
        server.show(1)
        yield server.url
