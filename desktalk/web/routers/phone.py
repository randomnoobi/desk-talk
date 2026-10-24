"""
The phone as the camera, on the phone page's own HTTPS server (create_phone_app):
    GET  /, /phone, /phone/  the camera page (phone_camera/index.html); its files are under /phone/
    GET  /api/phone/config   how many pictures a second to send, how big and how sharp, the highest real tilt, and
                             the picture size to ask the phone's camera for
    POST /api/phone/frame    one picture, with how the phone is tilted (X-Desk-Gravity) and how far it turned
                             the picture (X-Desk-Rotation)
    POST /api/phone/stop     the user pressed Stop on the phone
"""

from __future__ import annotations

import math
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from desktalk.config import CameraSettings
from desktalk.web.deps import App, key_check, other_camera, read_upload
from desktalk.web.files import page
from desktalk.web.schemas import Ok, PhoneFrameReply, PhoneSettings

NOT_A_PICTURE = 'not a JPEG image'
FULL_TURN = 360
WHOLE_NUMBER = re.compile(r'-?[0-9]+')      # plain 0-9 digits only: int() can't read every digit isdigit() allows

phone_router = APIRouter()


def parse_gravity(header: str | None, *, most: float) -> tuple[float, float, float] | None:
    """'x,y,z' from the X-Desk-Gravity header → (x, y, z), or None when it isn't three sensible numbers
    (`most`: camera.phone_tilt_max, in m/s²; a reading above it on any axis isn't a real one)."""
    if not header:
        return None
    try:
        values = [float(v) for v in header.split(',')[:3]]
    except ValueError:
        return None
    if len(values) != 3 or not all(math.isfinite(v) and abs(v) < most for v in values):
        return None
    return values[0], values[1], values[2]


def parse_rotation(header: str) -> int | None:
    """'-90' → 270: how far the phone turned the picture, or None when it didn't say (or didn't send a
    whole number, e.g. '--90')."""
    return int(header) % FULL_TURN if WHOLE_NUMBER.fullmatch(header) else None


# ── the phone page's own server ──────────────────────────────────────
@phone_router.get('/')
@phone_router.get('/phone')
@phone_router.get('/phone/')
def phone_page(app: App) -> FileResponse:
    return page(app.config.resolve(app.config.web.phone_folder) / 'index.html')


def phone_settings(camera: CameraSettings) -> PhoneSettings:
    return PhoneSettings(fps=camera.fps, max_side=camera.phone_max_side, jpeg_quality=camera.phone_jpeg_quality,
                         tilt_max=camera.phone_tilt_max, capture_width=camera.phone_capture_width,
                         capture_height=camera.phone_capture_height)


@phone_router.get('/api/phone/config')
def phone_config(app: App) -> PhoneSettings:
    return phone_settings(app.config.camera)


@phone_router.post('/api/phone/frame', response_model=PhoneFrameReply, response_model_exclude_none=True,
                   dependencies=[Depends(key_check('Pairing key does not match. Scan the QR code on your computer '
                                                   'again.'))])
async def phone_frame(request: Request, app: App) -> PhoneFrameReply:
    inbox, phone = app.phone_inbox, app.phone
    if inbox is None or phone is None:
        raise other_camera(app, 'phone')
    body = await read_upload(request, app.config.web.max_request_mb)
    if not await run_in_threadpool(inbox.push, body):      # decodes the JPEG: not on the event loop
        raise HTTPException(400, NOT_A_PICTURE)
    phone.set_motion(parse_gravity(request.headers.get('X-Desk-Gravity'), most=app.config.camera.phone_tilt_max),
                      parse_rotation(request.headers.get('X-Desk-Rotation', '')))
    return PhoneFrameReply(fps=app.source.fps, tip=app.camera_health.phone_tip())


@phone_router.post('/api/phone/stop', dependencies=[Depends(key_check('Pairing key does not match.'))])
def phone_stop(app: App) -> Ok:
    phone = app.phone
    if phone is None:
        raise other_camera(app, 'phone')
    phone.mark_stopped()
    return Ok()
