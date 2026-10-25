"""
How the characterization tests talk to the two web servers.

    main = WebClient.main_server(desktalk, links)   # what web_app/ and the Pi talk to (port 5000)
    phone = WebClient.phone_server(desktalk)        # the phone camera page's own HTTPS server (port 5443)
    reply = main.get('/api/live')                   # Reply: status, headers, body; reply.json()

This is the only file that knows the servers are FastAPI apps (desktalk/web/app.py). Requests go through
FastAPI's TestClient, which can also read the start of a reply that never ends (SSE, video) and hang up.
"""

from __future__ import annotations

import json as jsonlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from starlette.routing import Match, Mount

from desktalk.bootstrap import DeskTalk
from desktalk.web.app import create_app, create_phone_app
from desktalk.web.pairing import Links

THIS_COMPUTER = '127.0.0.1'         # where the browser on this computer connects from
CLIENT_PORT = 50000                 # the port the requests seem to come from (nothing listens on it)
STREAM_TIMEOUT_SEC = 10             # give up on a live stream (SSE, video) after this long
STREAM_MAX_BYTES = 2_000_000        # never read more than this from a live stream
AUTOMATIC_METHODS = {'HEAD', 'OPTIONS'}   # the web framework answers these by itself
FILES = '{path}'                    # how a folder of files shows up in routes(), e.g. /art/{path}


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
    def __init__(self, app: FastAPI):
        self._app = app
        self._clients: dict[str, TestClient] = {}

    @classmethod
    def main_server(cls, desktalk: DeskTalk, links: Links) -> WebClient:
        return cls(create_app(desktalk, links))

    @classmethod
    def phone_server(cls, desktalk: DeskTalk) -> WebClient:
        return cls(create_phone_app(desktalk))

    def _client(self, from_ip: str) -> TestClient:
        """A client whose requests come from `from_ip`. Without `with`, the app's lifespan never runs, so the
        background jobs stay off: the tests drive the desk by hand. Errors come back as replies (500), as
        from a real server, and redirects aren't followed, so they can be checked."""
        if from_ip not in self._clients:
            self._clients[from_ip] = TestClient(self._app, client=(from_ip, CLIENT_PORT),
                                                raise_server_exceptions=False, follow_redirects=False)
        return self._clients[from_ip]

    # ── requests ──
    def request(self, method: str, path: str, *, json: Any = None, data: bytes | None = None,
                headers: dict | None = None, from_ip: str = THIS_COMPUTER) -> Reply:
        r = self._client(from_ip).request(method, path, json=json, content=data, headers=headers or {})
        return Reply(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content)

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
                # A client of its own: this thread reads while the test's thread sends other requests.
                client = TestClient(self._app, client=(from_ip, CLIENT_PORT), raise_server_exceptions=False)
                with client.stream('GET', path) as r:
                    out['status'] = r.status_code
                    out['headers'] = {k.lower(): v for k, v in r.headers.items()}
                    if after_open:
                        after_open()
                    body = b''
                    for chunk in r.iter_raw():
                        body += chunk
                        if until(body) or len(body) >= STREAM_MAX_BYTES:
                            break
                    out['body'] = body
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
        """Every (method, URL rule) this server answers, not counting HEAD and OPTIONS.
        A folder of files is one rule: GET /art/{path}."""
        out = set()
        for context in iter_route_contexts(self._app.routes):        # the routes of included routers too
            if isinstance(context.route, Mount):
                out.add(('GET', f'{context.path}/{FILES}'))
            else:
                out |= {(m, context.path) for m in (context.methods or set()) - AUTOMATIC_METHODS}
        return out

    def route_for(self, method: str, path: str) -> tuple[str, str] | None:
        """The (method, URL rule) that answers this request, or None if no rule does."""
        scope = {'type': 'http', 'method': method, 'path': path.split('?')[0], 'root_path': '', 'headers': []}
        for context in iter_route_contexts(self._app.routes):
            match, _ = context.route.matches(scope)
            if match is Match.FULL:
                rule = context.path or ''
                return (method, f'{rule}/{FILES}' if isinstance(context.route, Mount) else rule)
        return None
