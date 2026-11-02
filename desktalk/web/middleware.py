"""
What every request on a server goes through, besides its route:

    OwnPagesOnly       403 for a write sent by a page from another web site. Without this a site open on a phone
                       on the same Wi-Fi could change things here: a simple POST needs no permission first
    SizeLimit          a request bigger than its limit gets 413, before the route reads it all
    LogFailedRequests  only requests that failed are logged: the Pi sends 3 pictures a second and the app
                       asks for news every 1.5 s, so logging every request would bury everything else
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

MB = 1024 * 1024
FAILED_FROM = 400          # status codes from here up are failures


def too_big(limit_bytes: int) -> HTTPException:
    return HTTPException(413, f'This request is bigger than the {limit_bytes / MB:g} MB this computer takes.')


class OwnPagesOnly:
    """A browser says which site's page sent a request (Origin). A write is refused unless that is this server, by
    the address the browser used (Host), or a site in `allowed`. Programs like the camera send no Origin, and pass."""

    READS = (b'GET', b'HEAD', b'OPTIONS')

    def __init__(self, app: ASGIApp, allowed: list[str], refusal: str):
        self.app = app
        self.allowed = {site.lower() for site in allowed}
        self.refusal = refusal

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or scope['method'].encode() in self.READS or self._may_write(scope):
            await self.app(scope, receive, send)
            return
        body = json.dumps({'error': self.refusal}).encode()
        await send({'type': 'http.response.start', 'status': 403,
                    'headers': [(b'content-type', b'application/json'),
                                (b'content-length', str(len(body)).encode())]})
        await send({'type': 'http.response.body', 'body': body})

    def _may_write(self, scope: Scope) -> bool:
        headers = dict(scope['headers'])
        origin = headers.get(b'origin')
        if origin is None:
            return True
        site = origin.decode('latin-1').lower()
        return site in self.allowed or urlsplit(site).netloc == headers.get(b'host', b'').decode('latin-1').lower()


class SizeLimit:
    """413 for a request that says (Content-Length) or turns out to be bigger than `limit_bytes`."""

    def __init__(self, app: ASGIApp, limit_bytes: int):
        self.app = app
        self.limit = limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        declared = dict(scope['headers']).get(b'content-length', b'')
        if declared.isdigit() and int(declared) > self.limit:
            error = too_big(self.limit)
            body = json.dumps({'error': error.detail}).encode()
            await send({'type': 'http.response.start', 'status': error.status_code,
                        'headers': [(b'content-type', b'application/json'),
                                    (b'content-length', str(len(body)).encode())]})
            await send({'type': 'http.response.body', 'body': body})
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
                if received > self.limit:
                    raise too_big(self.limit)       # the route reading the body gets it; it becomes the 413
            return message

        await self.app(scope, counted, send)


class LogFailedRequests:
    def __init__(self, app: ASGIApp, server: str):
        self.app = app
        self.server = server

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def noted(message: Message) -> None:
            if message['type'] == 'http.response.start' and message['status'] >= FAILED_FROM:
                client = scope.get('client') or ('?', 0)
                log.info('%s server: %s %s from %s → %d', self.server, scope['method'], scope['path'], client[0],
                         message['status'])
            await send(message)

        await self.app(scope, receive, noted)
