"""The line to the cloud server (desktalk/adapters/cloud/http_link.py), tried against a small server on this
computer, and the CloudLink contract on it and on the recording stand-in."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from desktalk.adapters.cloud.http_link import HttpCloudLink, claim
from desktalk.domain import CloudNotLinked, CloudUnavailable
from desktalk_shared.cloud_api import ClaimReply
from tests.contracts.cloud_link_contract import EVENTS, CloudLinkContract
from tests.fakes import RecordingCloudLink

TOKEN = 'good-token-123'
CODE = 'ABCD-EFGH'


class StandInServer:
    """A cloud server on 127.0.0.1 that keeps the ids it gets. `status` (not 200) is answered to every events
    request; `body` replaces a good reply's body; `delay` makes it slow."""

    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.kept: set[str] = set()
        self.status = 200
        self.redirect_to = ''
        self.body: bytes | None = None
        self.delay = 0.0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:       # keep the test output quiet
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(length)
                data = json.loads(raw) if raw else {}
                outer.requests.append({'path': self.path, 'headers': dict(self.headers), 'json': data})
                if outer.delay:
                    time.sleep(outer.delay)
                if self.path == '/v1/devices/claim':
                    ok = data.get('code') == CODE
                    self._reply(200 if ok else 404, {'device_id': 'dev-1', 'token': TOKEN} if ok
                                else {'detail': 'That code is not valid.'})
                elif self.headers.get('Authorization') != f'Bearer {TOKEN}':
                    self._reply(401, {'detail': 'Unknown token.'})
                elif outer.status != 200:
                    self._reply(outer.status, {'detail': 'nope'}, location=outer.redirect_to)
                else:
                    ids = [e['id'] for e in data['events']]
                    fresh = [i for i in ids if i not in outer.kept]
                    outer.kept.update(fresh)
                    reply = {'accepted': len(fresh), 'repeats': len(ids) - len(fresh), 'refused': []}
                    self._reply(200, reply, raw=outer.body)

            def _reply(self, status: int, data: Any, raw: bytes | None = None, location: str = '') -> None:
                body = raw if raw is not None else json.dumps(data).encode()
                self.send_response(status)
                if location:
                    self.send_header('Location', location)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.address = f'http://127.0.0.1:{self._server.server_address[1]}'
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': 0.01},
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def server():
    stand_in = StandInServer()
    yield stand_in
    stand_in.stop()


def link_to(server: StandInServer, token: str = TOKEN, timeout_sec: float = 5) -> HttpCloudLink:
    return HttpCloudLink(server.address, token, timeout_sec=timeout_sec)


# ── the contract, on the real line and on the stand-in ──
class TestHttpCloudLink(CloudLinkContract):
    @pytest.fixture
    def link(self, server):
        return link_to(server)

    @pytest.fixture
    def unknown_link(self, server):
        return link_to(server, token='a-removed-token')

    @pytest.fixture
    def dead_link(self):
        stopped = StandInServer()
        stopped.stop()                                  # nothing listens on its port any more
        return link_to(stopped, timeout_sec=2)


class TestRecordingCloudLink(CloudLinkContract):
    @pytest.fixture
    def link(self):
        return RecordingCloudLink()

    @pytest.fixture
    def unknown_link(self):
        return RecordingCloudLink(failure=CloudNotLinked('unknown token'))

    @pytest.fixture
    def dead_link(self):
        return RecordingCloudLink(failure=CloudUnavailable('off'))


# ── what only the real line does ──
def test_the_token_goes_in_the_authorization_header_and_the_events_in_a_json_body(server):
    link_to(server).send(EVENTS, caught_up=True)
    [request] = server.requests
    assert request['path'] == '/v1/events'
    assert request['headers']['Authorization'] == f'Bearer {TOKEN}'
    assert request['headers']['Content-Type'] == 'application/json'
    assert request['json'] == {'events': EVENTS, 'caught_up': True}


def test_the_refused_events_come_back_with_their_reasons(server):
    server.body = json.dumps({'accepted': 1, 'repeats': 0, 'refused': [{'id': 'a-2', 'why': 'too old'}]}).encode()
    reply = link_to(server).send(EVENTS, caught_up=False)
    assert [(r.id, r.why) for r in reply.refused] == [('a-2', 'too old')]


def test_the_server_address_may_end_in_a_slash(server):
    HttpCloudLink(server.address + '/', TOKEN, timeout_sec=5).send(EVENTS, caught_up=False)
    assert server.requests[0]['path'] == '/v1/events'


@pytest.mark.parametrize('status', [429, 500, 502, 503])
def test_busy_or_broken_means_try_later(server, status):
    server.status = status
    with pytest.raises(CloudUnavailable) as raised:
        link_to(server).send(EVENTS, caught_up=False)
    assert str(status) in str(raised.value)


@pytest.mark.parametrize('status', [400, 404, 413])
def test_any_other_error_status_is_try_later_too(server, status):
    server.status = status
    with pytest.raises(CloudUnavailable):
        link_to(server).send(EVENTS, caught_up=False)


@pytest.mark.parametrize('status', [301, 302, 307, 308])
def test_a_redirect_is_never_followed_so_the_token_goes_nowhere_else(server, status):
    elsewhere = StandInServer()
    try:
        server.status, server.redirect_to = status, elsewhere.address + '/v1/events'
        with pytest.raises(CloudUnavailable):
            link_to(server).send(EVENTS, caught_up=False)
        assert elsewhere.requests == []
    finally:
        elsewhere.stop()


def test_a_reply_that_is_not_the_expected_json_is_try_later(server):
    server.body = b'<html>a proxy error page</html>'
    with pytest.raises(CloudUnavailable):
        link_to(server).send(EVENTS, caught_up=False)


def test_a_server_that_takes_too_long_is_try_later(server):
    server.delay = 1.0
    started = time.monotonic()
    with pytest.raises(CloudUnavailable):
        link_to(server, timeout_sec=0.2).send(EVENTS, caught_up=False)
    assert time.monotonic() - started < 0.9


@pytest.mark.parametrize('how', ['unknown', 'broken'])
def test_the_token_is_never_in_an_error_message(server, how):
    server.status = 503 if how == 'broken' else 200
    link = link_to(server, token=TOKEN if how == 'broken' else 'secret-wrong-token')
    with pytest.raises((CloudUnavailable, CloudNotLinked)) as raised:
        link.send(EVENTS, caught_up=False)
    assert 'secret-wrong-token' not in str(raised.value) and TOKEN not in str(raised.value)


@pytest.mark.parametrize('address', ['file:///etc/passwd', 'ftp://example.com', 'example.com:8000', ''])
def test_only_web_addresses_are_accepted(address):
    with pytest.raises(ValueError):
        HttpCloudLink(address, TOKEN, timeout_sec=5)


# ── linking with a one-time code ──
def test_a_good_code_gives_the_device_and_its_token(server):
    reply = claim(server.address, CODE, 'My desk', timeout_sec=5)
    assert reply == ClaimReply(device_id='dev-1', token=TOKEN)
    assert server.requests[0]['json'] == {'code': CODE, 'name': 'My desk'}


def test_a_code_the_server_does_not_accept_says_so(server):
    with pytest.raises(CloudNotLinked):
        claim(server.address, 'wrong', 'My desk', timeout_sec=5)


def test_claiming_from_a_server_that_is_off_is_try_later():
    stopped = StandInServer()
    stopped.stop()
    with pytest.raises(CloudUnavailable):
        claim(stopped.address, CODE, 'My desk', timeout_sec=2)
