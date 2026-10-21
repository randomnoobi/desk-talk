"""
What the routes share:

    Desk           the running app (desktalk/bootstrap.py → DeskTalk): def live(desk: Desk) ...
    ShownLinks     the Pi command and the phone link (desktalk/web/pairing.py)
    Streams        the open live streams (desktalk/web/streams.py)
    FromHere       True when the request comes from this computer (the Pi command and the phone link are
                   only shown there, not to every device on the Wi-Fi)
    key_check(...) 403 unless the request carries the pairing key in X-Desk-Key
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from desktalk.bootstrap import DeskTalk
from desktalk.config import ListSize
from desktalk.util import local_day
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


Desk = Annotated[DeskTalk, Depends(_desktalk)]
ShownLinks = Annotated[Links, Depends(_links)]
Streams = Annotated[LiveStreams, Depends(_streams)]
FromHere = Annotated[bool, Depends(_from_here)]


def key_check(problem: str) -> Callable[[Request, DeskTalk], None]:
    """A dependency: 403 {"error": problem} unless X-Desk-Key is the pairing key."""
    def check(request: Request, desk: Desk) -> None:
        if request.headers.get('X-Desk-Key') != desk.pair_key:
            raise HTTPException(403, problem)
    return check


def other_camera(desk: DeskTalk, kind: str) -> HTTPException:
    """409: the camera in the settings is another one, so an upload from `kind` would go nowhere."""
    return HTTPException(409, f'This computer is set to camera.source: {desk.source_kind}. '
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


def today(desk: DeskTalk) -> str:
    return local_day(desk.clock.now())


def uptime_sec(desk: DeskTalk) -> int:
    return round(desk.clock.now() - desk.started_at)


def camera_index(desk: DeskTalk) -> int:
    """The camera the live view shows (0 until a picture came)."""
    with desk.desk.lock:
        cameras = sorted(desk.desk.cameras)
    return cameras[0] if cameras else 0


__all__ = ['Desk', 'FromHere', 'ShownLinks', 'Streams', 'camera_index', 'other_camera', 'key_check', 'list_size',
           'read_upload', 'today', 'uptime_sec']
