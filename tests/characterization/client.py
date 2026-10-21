"""
How the characterization tests talk to the two web servers.

    main = WebClient.main_server(app)     # what web_app/ and the Pi talk to (port 5000)
    phone = WebClient.phone_server(app)   # the phone camera page's own HTTPS server (port 5443)
    reply = main.get('/api/live')         # Reply: status, headers, body; reply.json()

This is the only file that knows the servers are Flask apps. Phase 3 (FastAPI) changes the inside of
WebClient to use FastAPI's TestClient; the tests and the saved shapes stay as they are.
"""

from __future__ import annotations

import json as jsonlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from werkzeug.exceptions import HTTPException
from werkzeug.routing import RequestRedirect

from desktalk.web.phone_server import create_phone_app
from desktalk.web.routes import create_web_app

THIS_COMPUTER = '127.0.0.1'         # where the browser on this computer connects from
STREAM_TIMEOUT_SEC = 10             # give up on a live stream (SSE, video) after this long
STREAM_MAX_BYTES = 2_000_000        # never read more than this from a live stream
AUTOMATIC_METHODS = {'HEAD', 'OPTIONS'}   # the web framework answers these by itself


@dataclass
class Reply:
    status: int
    headers: dict[str, str]          # header names in lower case
    body: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get('content-type', '')

    def json(self) -> Any:
        return jsonlib.loads(self.body)


class StreamTimeout(AssertionError):
    pass


class WebClient:
    def __init__(self, flask_app):
        self._app = flask_app
        self._client = flask_app.test_client()

    @classmethod
    def main_server(cls, app) -> WebClient:
        return cls(create_web_app(app))

    @classmethod
    def phone_server(cls, app) -> WebClient:
        return cls(create_phone_app(app))

    # ── requests ──
    def request(self, method: str, path: str, *, json: Any = None, data: bytes | None = None,
                headers: dict | None = None, from_ip: str = THIS_COMPUTER) -> Reply:
        kw: dict[str, Any] = {'method': method, 'headers': headers or {}, 'environ_base': {'REMOTE_ADDR': from_ip}}
        if json is not None:
            kw['json'] = json
        if data is not None:
            kw['data'] = data
        r = self._client.open(path, **kw)
        return Reply(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.get_data())

    def get(self, path: str, **kw) -> Reply:
        return self.request('GET', path, **kw)

    def post(self, path: str, **kw) -> Reply:
        return self.request('POST', path, **kw)

    def put(self, path: str, **kw) -> Reply:
        return self.request('PUT', path, **kw)

    def stream(self, path: str, *, until: Callable[[bytes], bool], after_open: Callable[[], Any] | None = None,
               meanwhile: Callable[[], Any] | None = None, from_ip: str = THIS_COMPUTER) -> Reply:
        """GET a reply that never ends (SSE, video). Reads until `until(body so far)` is true, then hangs up.

        `after_open` runs once the status and headers are in, before reading on, e.g. to send a chat
        message that should then come through the stream. `meanwhile` runs in the calling thread while
        the stream is being opened and read, for streams that send nothing (not even headers) until
        something happens. Fails, instead of waiting forever, if nothing usable arrives within
        STREAM_TIMEOUT_SEC."""
        out: dict[str, Any] = {}

        def run():
            try:
                r = self._client.get(path, buffered=False, environ_base={'REMOTE_ADDR': from_ip})
                try:
                    out['status'] = r.status_code
                    out['headers'] = {k.lower(): v for k, v in r.headers.items()}
                    if after_open:
                        after_open()
                    body = b''
                    for chunk in r.response:
                        body += chunk.encode() if isinstance(chunk, str) else chunk
                        if until(body) or len(body) >= STREAM_MAX_BYTES:
                            break
                    out['body'] = body
                finally:
                    r.close()
            except Exception as exc:      # handed to the test's thread below
                out['error'] = exc

        t = threading.Thread(target=run, daemon=True, name=f'stream {path}')
        t.start()
        if meanwhile:
            meanwhile()
        t.join(STREAM_TIMEOUT_SEC)
        if t.is_alive():
            raise StreamTimeout(f'{path} sent nothing usable within {STREAM_TIMEOUT_SEC} s')
        if 'error' in out:
            raise out['error']
        return Reply(out['status'], out['headers'], out['body'])

    # ── which URLs exist ──
    def routes(self) -> set[tuple[str, str]]:
        """Every (method, URL rule) this server answers, not counting HEAD and OPTIONS."""
        return {(m, rule.rule) for rule in self._app.url_map.iter_rules() for m in rule.methods - AUTOMATIC_METHODS}

    def route_for(self, method: str, path: str) -> tuple[str, str] | None:
        """The (method, URL rule) that answers this request, or None if no rule does."""
        try:
            rule, _ = self._app.url_map.bind('localhost').match(path.split('?')[0], method=method, return_rule=True)
        except RequestRedirect:
            return None
        except HTTPException:
            return None
        return method, rule.rule
