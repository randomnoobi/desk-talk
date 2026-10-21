"""
The phone as the camera.

On the main server:
    GET  /api/phone/info     is the phone the camera, is it sending, and (on this computer only) its link + QR code

On the phone page's own HTTPS server (create_phone_app):
    GET  /, /phone, /phone/  the camera page (phone_camera/index.html); its files are under /phone/
    GET  /api/phone/config   how many pictures a second to send
    POST /api/phone/frame    one picture, with how the phone is tilted (X-Desk-Gravity) and how far it turned
                             the picture (X-Desk-Rotation)
    POST /api/phone/stop     the user pressed Stop on the phone
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from desktalk.web.deps import Desk, FromHere, ShownLinks, key_check, other_camera, read_upload
from desktalk.web.files import page
from desktalk.web.schemas import Ok, PhoneFrameReply

NOT_A_PICTURE = 'not a JPEG image'
GRAVITY_MAX = 50          # m/s²: a tilt reading above this on any axis isn't a real one (gravity is 9.8)
FULL_TURN = 360

router = APIRouter()
phone_router = APIRouter()


def parse_gravity(header: str | None) -> tuple[float, float, float] | None:
    """'x,y,z' from the X-Desk-Gravity header → (x, y, z), or None when it isn't three sensible numbers."""
    if not header:
        return None
    try:
        values = [float(v) for v in header.split(',')[:3]]
    except ValueError:
        return None
    if len(values) != 3 or not all(math.isfinite(v) and abs(v) < GRAVITY_MAX for v in values):
        return None
    return values[0], values[1], values[2]


def parse_rotation(header: str) -> int | None:
    """'-90' → 270: how far the phone turned the picture, or None when it didn't say."""
    return int(header) % FULL_TURN if header.lstrip('-').isdigit() else None


@router.get('/api/phone/info')
def phone_info(desk: Desk, links: ShownLinks, from_here: FromHere) -> dict[str, Any]:
    phone = desk.phone_source is not None
    return {'phone_mode': phone, 'connected': phone and desk.desk_status.camera_connected(), 'fps': desk.source.fps,
            'url': links.phone_url if from_here else '', 'qr_svg': links.phone_qr_svg if from_here else None}


# ── the phone page's own server ──────────────────────────────────────
@phone_router.get('/')
@phone_router.get('/phone')
@phone_router.get('/phone/')
def phone_page(desk: Desk) -> FileResponse:
    return page(desk.config.resolve(desk.config.web.phone_folder) / 'index.html')


@phone_router.get('/api/phone/config')
def phone_config(desk: Desk) -> dict[str, Any]:
    return {'fps': desk.config.camera.fps}


@phone_router.post('/api/phone/frame', response_model=PhoneFrameReply, response_model_exclude_none=True,
                   dependencies=[Depends(key_check('Pairing key does not match. Scan the QR code on your computer '
                                                   'again.'))])
async def phone_frame(request: Request, desk: Desk) -> PhoneFrameReply:
    source = desk.phone_source
    if source is None:
        raise other_camera(desk, 'phone')
    body = await read_upload(request, desk.config.web.max_request_mb)
    if not await run_in_threadpool(source.push, body):      # decodes the JPEG: not on the event loop
        raise HTTPException(400, NOT_A_PICTURE)
    source.set_motion(parse_gravity(request.headers.get('X-Desk-Gravity')),
                      parse_rotation(request.headers.get('X-Desk-Rotation', '')))
    return PhoneFrameReply(fps=source.fps, tip=desk.camera_health.phone_tip())


@phone_router.post('/api/phone/stop', dependencies=[Depends(key_check('Pairing key does not match.'))])
def phone_stop(desk: Desk) -> Ok:
    source = desk.phone_source
    if source is None:
        raise other_camera(desk, 'phone')
    source.mark_stopped()
    return Ok()
