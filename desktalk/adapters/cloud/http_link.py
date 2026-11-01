"""
The line to a cloud server over plain HTTP, with the standard library only (as the Pi's own net.py does):

    link = HttpCloudLink('http://192.168.1.9:8000', token, timeout_sec=10)
    reply = link.send(events, caught_up=False)       # an IngestReply; CloudUnavailable / CloudNotLinked when it fails

    reply = claim('http://192.168.1.9:8000', code, 'My desk', timeout_sec=10)     # a ClaimReply: device id + token

The token goes in the `Authorization: Bearer` header and nowhere else: never in the address, the body or an error
message, and a redirect is never followed. Each status becomes one of two errors: 401 is CloudNotLinked (the
server doesn't know us), and no answer, a slow answer, 429, 5xx, a garbled answer or any other status is
CloudUnavailable (try again later).
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.domain import CloudNotLinked, CloudUnavailable
from desktalk_shared._read import WireError
from desktalk_shared.cloud_api import ClaimReply, IngestReply

OK, UNAUTHORIZED = 200, 401
CLIENT_ERRORS = range(400, 500)


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it would send the token on to wherever the reply points. (A redirect then counts as
    an odd status, so the sender tries again later.)"""

    def redirect_request(self, *_reply: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirects)


def _address(server: str) -> str:
    """The server's address without a trailing slash. ValueError unless it is a web address (http or https): the
    standard library would also open file: and ftp: addresses."""
    parts = urllib.parse.urlsplit(server.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError(f'{server!r} is not a web address: write it like http://192.168.1.9:8000')
    return server.strip().rstrip('/')


def _why(exc: BaseException, timeout_sec: float) -> str:
    """Plain words for a request that got no answer. Never has the request in it (so never the token)."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return f'no answer within {timeout_sec:g} s'
    if isinstance(reason, ConnectionRefusedError):
        return "it's not running (connection refused)"
    if isinstance(reason, (ConnectionResetError, http.client.RemoteDisconnected, http.client.BadStatusLine)):
        return 'it hung up without answering'
    return str(reason) or type(reason).__name__


def _post(url: str, body: Mapping[str, Any], *, timeout_sec: float, token: str | None = None) -> tuple[int, bytes]:
    """The status and body of the reply, error statuses included. Raises CloudUnavailable when there is no reply."""
    headers = {'Content-Type': 'application/json'}
    if token is not None:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
    try:
        with _OPENER.open(request, timeout=timeout_sec) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                return exc.code, exc.read()
            except (OSError, http.client.HTTPException) as cut_off:         # it hung up half-way through
                raise CloudUnavailable(_why(cut_off, timeout_sec)) from None
    except (OSError, http.client.HTTPException) as exc:
        raise CloudUnavailable(_why(exc, timeout_sec)) from None


def _json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        raise CloudUnavailable('the server gave an answer that is not JSON') from None


class HttpCloudLink:
    """The CloudLink (desktalk/ports.py) for a server at `server`, known by `token`."""

    def __init__(self, server: str, token: str, *, timeout_sec: float):
        self._url = _address(server) + '/v1/events'
        self._token = token
        self._timeout = timeout_sec

    def send(self, events: Sequence[Mapping[str, Any]], *, caught_up: bool) -> IngestReply:
        status, body = _post(self._url, {'events': list(events), 'caught_up': caught_up},
                             timeout_sec=self._timeout, token=self._token)
        if status == UNAUTHORIZED:
            raise CloudNotLinked('the server does not know this computer (401)')
        if status != OK:
            raise CloudUnavailable(f'the server answered {status}')
        try:
            return IngestReply.from_dict(_json(body))
        except WireError as exc:
            raise CloudUnavailable(f'the server gave an answer that makes no sense ({exc})') from None


def claim(server: str, code: str, name: str, *, timeout_sec: float) -> ClaimReply:
    """Trade a one-time code for this device's id and token. CloudNotLinked when the server doesn't accept the code
    (any 4xx: a wrong, used or expired code all get the same answer), CloudUnavailable when it can't be reached."""
    status, body = _post(_address(server) + '/v1/devices/claim', {'code': code, 'name': name}, timeout_sec=timeout_sec)
    if status in CLIENT_ERRORS and status != 429:
        raise CloudNotLinked(f'the server did not accept that code ({status})')
    if status != OK:
        raise CloudUnavailable(f'the server answered {status}')
    try:
        return ClaimReply.from_dict(_json(body))
    except WireError as exc:
        raise CloudUnavailable(f'the server gave an answer that makes no sense ({exc})') from None
