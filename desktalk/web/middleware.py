"""
What every request on a server goes through, besides its route:

    AnyPageMayAsk      Access-Control-Allow-Origin: * on every reply of the main server, as the Flask server
                       sent it (Starlette's CORSMiddleware only adds it when the request says where it's from)
    SizeLimit          a request bigger than its limit gets 413, before the route reads it all
    LogFailedRequests  only requests that failed are logged: the Pi sends 3 pictures a second and the app
                       asks for news every 1.5 s, so logging every request would bury everything else
"""

from __future__ import annotations

import json
import logging

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

MB = 1024 * 1024
FAILED_FROM = 400          # status codes from here up are failures


def too_big(limit_bytes: int) -> HTTPException:
    return HTTPException(413, f'This request is bigger than the {limit_bytes / MB:g} MB this computer takes.')


class AnyPageMayAsk:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers.setdefault('access-control-allow-origin', '*')
            await send(message)

        await self.app(scope, receive, send_with_header)


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
