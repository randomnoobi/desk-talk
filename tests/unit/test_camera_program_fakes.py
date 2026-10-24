"""
Fakes for the camera program's tests (raspberry_pi/camera/): a clock, a camera, and small web
servers on 127.0.0.1 that stand in for the detector container and for this computer.

The camera program runs on the Pi's own Python, so its tests import neither desktalk nor
tests.conftest / tests.fakes (those need the app's packages). This file holds no tests itself.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

from desktalk_shared.detection import BBox, Detection
from desktalk_shared.detector_api import API_VERSION, DetectorHealth, DetectReply, ModelSettings, ServiceState
from desktalk_shared.wire import UploadInfo, WireError, decode_upload

JPEG = b'\xff\xd8\xff\xe0 a picture \xff\xd9'          # the camera program never decodes pictures
KEY = 'testkey'


class FakeClock:
    """A clock that only moves when the test says so (advance) or when code calls sleep()."""

    def __init__(self, start: float = 1_760_000_000.0):
        self._now = float(start)
        self._monotonic = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now += seconds
        self._monotonic += seconds


class FakeCamera:
    """Hands out the given pictures in a loop. Set `broken` to an exception to make capture() fail."""

    name = 'fake'

    def __init__(self, pictures=(JPEG,), lux: float | None = None):
        self.pictures = list(pictures)
        self.lux = lux
        self.taken = 0
        self.broken: Exception | None = None
        self.closed = False

    def capture(self):
        if self.broken:
            raise self.broken
        jpeg = self.pictures[self.taken % len(self.pictures)]
        self.taken += 1
        return jpeg, self.lux

    def close(self):
        self.closed = True


# ── web servers ───────────────────────────────────────────────────────
@dataclass
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


HANG_UP = object()       # a route returns this to close the connection without answering


class FakeServer:
    """A web server on 127.0.0.1 in a background thread. `routes` maps (method, path) to a function
    taking a Request and returning (status, dict or bytes), or HANG_UP. Every request is kept in
    `requests`. `hold` makes every request wait (no answer) until the server stops; `away` hangs up
    on every request, like a program that's gone."""

    def __init__(self):
        self.requests: list[Request] = []
        self.routes: dict[tuple[str, str], Callable[[Request], Any]] = {}
        self.hold = False
        self.away = False
        self._release = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                size = int(self.headers.get('Content-Length') or 0)
                req = Request(self.command, self.path, {k.lower(): v for k, v in self.headers.items()},
                              self.rfile.read(size))
                server.requests.append(req)
                if server.hold:
                    server._release.wait(10)
                    self.close_connection = True
                    return
                route = server.routes.get((self.command, self.path))
                result = HANG_UP if server.away else route(req) if route else (404, {'detail': 'Not Found'})
                if result is HANG_UP:
                    self.close_connection = True
                    return
                status, payload = cast(tuple[int, Any], result)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _handle

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.block_on_close = False
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._release.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    def paths(self, method: str | None = None) -> list[str]:
        return [r.path for r in self.requests if method is None or r.method == method]


def cup(x1=10, y1=20, x2=50, y2=60, conf=0.8) -> Detection:
    return Detection('cup', conf, BBox(x1, y1, x2, y2))


MODEL = ModelSettings(model='yolo11s.pt', format='ncnn', image_size=480, confidence=0.35, half=False)


class FakeContainer(FakeServer):
    """The detector container: /detect answers with `found`; /health and PUT /model as the real service.
    Set `detect_status` (e.g. 503) to make /detect fail, and `state` for what /health says."""

    def __init__(self, found=None, model: ModelSettings | None = MODEL):
        super().__init__()
        self.found = tuple(found) if found is not None else (cup(),)
        self.model = model
        self.state = ServiceState.READY
        self.message = ''
        self.api_version = API_VERSION
        self.detect_status = 200
        self.models_put: list[ModelSettings] = []
        self.routes[('POST', '/detect')] = self._detect
        self.routes[('GET', '/health')] = lambda req: (200, self._health().to_dict())
        self.routes[('PUT', '/model')] = self._put_model

    def _health(self) -> DetectorHealth:
        return DetectorHealth(state=self.state, model=self.model, message=self.message, api_version=self.api_version)

    def _detect(self, req):
        if self.detect_status != 200:
            return self.detect_status, {'detail': 'no model loaded yet'}
        return 200, DetectReply(detections=self.found, detect_ms=123.0, width=640, height=480,
                                model='yolo11s.pt (ncnn, 480)').to_dict()

    def _put_model(self, req):
        self.models_put.append(ModelSettings.from_dict(json.loads(req.body)))
        return 200, DetectorHealth(state=ServiceState.LOADING, model=self.model, message='loading',
                                   api_version=self.api_version).to_dict()


@dataclass
class Upload:
    jpeg: bytes
    info: UploadInfo
    key: str


class FakeComputer(FakeServer):
    """This computer: /api/pi/config and /api/pi/frame (upload format 2, read with decode_upload)."""

    def __init__(self, pi: dict | None = None, key: str = KEY):
        super().__init__()
        self.key = key
        self.pi = pi if pi is not None else pi_settings()
        self.camera_source = 'pi'
        self.uploads: list[Upload] = []
        self.bad_uploads: list[str] = []
        self.routes[('GET', '/api/pi/config')] = self._config
        self.routes[('POST', '/api/pi/frame')] = self._frame

    def _key_ok(self, req) -> bool:
        return req.headers.get('x-desk-key') == self.key

    def _config(self, req):
        if not self._key_ok(req):
            return 403, {'error': 'Pairing key does not match. Copy the command printed by server.py.'}
        return 200, {'pi': self.pi, 'camera_source': self.camera_source}

    def _frame(self, req):
        if not self._key_ok(req):
            return 403, {'error': 'Pairing key does not match. Copy the command printed by server.py.'}
        try:
            jpeg, info = decode_upload(req.body, req.headers.get('content-type', ''))
        except WireError as exc:
            self.bad_uploads.append(str(exc))
            return 400, {'error': str(exc)}
        self.uploads.append(Upload(jpeg, info, req.headers['x-desk-key']))
        return 200, {'ok': True, 'fps': 3.0, 'tip': ''}


# ── settings ──────────────────────────────────────────────────────────
def pi_settings(**changes) -> dict:
    """A complete `pi:` section, as config/settings.yaml has it (plus the camera program's new settings)."""
    pi = {
        'fps': 3, 'width': 1280, 'height': 720, 'jpeg_quality': 80, 'photo_dir': '~/desk-talk-photos',
        'delete_every_min': 10, 'keep_last_min': 0, 'min_free_mb': 300, 'upload_timeout_sec': 5,
        'upload_retry_start_sec': 1, 'upload_retry_max_sec': 10, 'hot_warning_c': 75, 'clock_off_warning_sec': 5,
        'camera_warmup_sec': 1, 'pause_after_error_sec': 0.5, 'status_every_sec': 30, 'health_every_sec': 3,
        'settings_check_every_sec': 60, 'restart_after_missed_pictures': 30, 'camera': 'auto', 'error_text_chars': 200,
        'detector': {
            'model': 'yolo11s.pt', 'format': 'ncnn', 'image_size': 480, 'confidence': 0.35, 'half': False,
            'url': 'http://127.0.0.1:8100', 'wait_per_picture_sec': 2, 'failures_before_pause': 3,
            'pause_start_sec': 5, 'pause_max_sec': 60, 'health_check_every_sec': 10,
        },
    }
    detector = changes.pop('detector', {})
    pi.update(changes)
    pi['detector'].update(detector)
    return pi
