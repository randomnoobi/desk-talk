"""
The Raspberry Pi's camera program (raspberry_pi/camera/):

    GET  /api/pi/config   its settings (settings.yaml → pi, detector included), so they live in one place
    POST /api/pi/frame    one picture. Upload format 2 (multipart: the picture plus what the Pi's detector
                          found, desktalk_shared.wire.decode_upload), or format 1 from the old camera_node.py
                          (the picture as the body, X-... headers)

Both need the pairing key in X-Desk-Key.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from desktalk.web.deps import Desk, key_check, other_camera, read_upload
from desktalk.web.schemas import PiFrameReply
from desktalk_shared.wire import WireError, decode_upload, info_from_v1_headers

KEY_PROBLEM = 'Pairing key does not match. Copy the command printed by server.py.'
NOT_A_PICTURE = 'not a JPEG image'
FORMAT_2 = 'multipart/form-data'

router = APIRouter(dependencies=[Depends(key_check(KEY_PROBLEM))])


@router.get('/api/pi/config')
def pi_config(desk: Desk) -> dict[str, Any]:
    return {'pi': desk.config.pi.model_dump(mode='json'), 'camera_source': desk.source_kind}


@router.post('/api/pi/frame', response_model=PiFrameReply)
async def pi_frame(request: Request, desk: Desk) -> PiFrameReply:
    source = desk.pi_source
    if source is None:
        raise other_camera(desk, 'pi')
    body = await read_upload(request, desk.config.web.pi_upload_max_mb)
    content_type = request.headers.get('content-type', '')
    if content_type.lower().startswith(FORMAT_2):
        try:
            jpeg, info = decode_upload(body, content_type)
        except WireError as exc:
            raise HTTPException(400, str(exc)) from None
    else:
        jpeg, info = body, info_from_v1_headers(request.headers)
    if not await run_in_threadpool(source.push, jpeg, info):      # decodes the JPEG: not on the event loop
        raise HTTPException(400, NOT_A_PICTURE)
    return PiFrameReply(fps=source.fps, tip=desk.camera_health.phone_tip())
