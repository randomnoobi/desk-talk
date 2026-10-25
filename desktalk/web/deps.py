"""
What the routes share:

    App            the running app (desktalk/bootstrap.py → DeskTalk): def live(app: App) ...
    ShownLinks     the Pi command and the phone link (desktalk/web/pairing.py)
    Streams        the open live streams (desktalk/web/streams.py)
    FromHere       True when the request comes from this computer (the Pi command and the phone link are
                   only shown there, not to every device on the Wi-Fi)
    DevOnly        403 unless the request comes from this computer and, when it says which page sent it
                   (Origin), from the server's own page or the Vite dev server's (the developer page's routes)
    key_check(...) 403 unless the request carries the pairing key in X-Desk-Key
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from desktalk.bootstrap import DeskTalk
from desktalk.config import ListSize
from desktalk.web.middleware import MB, too_big
from desktalk.web.pairing import Links
from desktalk.web.streams import LiveStreams

LOCAL_ADDRESSES = ('127.0.0.1', '::1')


def _desktalk(request: Request) -> DeskTalk:
    return request.app.state.desktalk


def _links(request: Request) -> Links:
    return request.app.state.links


def _streams(request: Request) -> LiveStreams:
    return request.app.state.streams


def _from_here(request: Request) -> bool:
    return request.client is not None and request.client.host in LOCAL_ADDRESSES


App = Annotated[DeskTalk, Depends(_desktalk)]
ShownLinks = Annotated[Links, Depends(_links)]
Streams = Annotated[LiveStreams, Depends(_streams)]
FromHere = Annotated[bool, Depends(_from_here)]


def _own_pages(app: DeskTalk) -> set[str]:
    """The pages that may use the developer page's routes: this server's own, and the Vite dev server's."""
    web, vite = app.config.web.port, app.config.dev.vite_port
    return {f'http://localhost:{web}', f'http://127.0.0.1:{web}', f'http://localhost:{vite}'}


def dev_only(request: Request, app: App) -> None:
    """403 for a request from another device on the network, or one a web page from somewhere else sent
    (a browser always says which page sent a POST, in Origin), so no other page open in your browser can start
    a test run or a query. The main server lets any page ask (CORS *); this is on top of that."""
    words = app.config.prompts.dev
    if not _from_here(request):
        raise HTTPException(403, words.other_computer)
    origin = request.headers.get('origin')
    if origin is not None and origin not in _own_pages(app):
        raise HTTPException(403, words.other_web_page)


DevOnly = Depends(dev_only)


def key_check(problem: str) -> Callable[[Request, DeskTalk], None]:
    """A dependency: 403 {"error": problem} unless X-Desk-Key is the pairing key."""
    def check(request: Request, app: App) -> None:
        given = request.headers.get('X-Desk-Key')
        # Compared in a way that takes as long for a near miss as for a wild guess, so the key can't be
        # worked out bit by bit from how fast the replies come. As bytes, so any letters in the header work.
        if given is None or not secrets.compare_digest(given.encode(), app.pair_key.encode()):
            raise HTTPException(403, problem)
    return check


def other_camera(app: DeskTalk, kind: str) -> HTTPException:
    """409: the camera in the settings is another one, so an upload from `kind` would go nowhere."""
    return HTTPException(409, f'This computer is set to camera.source: {app.source_kind}. '
                              f'Set it to {kind} in config/settings.yaml.')


async def read_upload(request: Request, limit_mb: float) -> bytes:
    """The whole request body; 413 if it is (or says it is) bigger than limit_mb."""
    limit = int(limit_mb * MB)
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > limit:
        raise too_big(limit)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise too_big(limit)
    return bytes(body)


def list_size(asked: str | None, size: ListSize) -> int:
    """How many entries a list returns: `asked` (?limit=) kept between 1 and size.max, else size.default.
    400 when `asked` isn't a whole number."""
    if asked is None:
        return size.default
    try:
        wanted = int(asked)
    except ValueError:
        raise HTTPException(400, f'limit must be a whole number (got "{asked}")') from None
    return min(max(wanted, 1), size.max)


def today(app: DeskTalk) -> str:
    return app.clock.local_day(app.clock.now())


def uptime_sec(app: DeskTalk) -> int:
    return round(app.clock.now() - app.started_at)


def camera_index(app: DeskTalk) -> int:
    """The camera the live view shows (0 until a picture came)."""
    cameras = app.live_pictures.camera_numbers()
    return cameras[0] if cameras else 0


__all__ = ['App', 'DevOnly', 'FromHere', 'ShownLinks', 'Streams', 'camera_index', 'dev_only', 'other_camera',
           'key_check', 'list_size', 'read_upload', 'today', 'uptime_sec']
