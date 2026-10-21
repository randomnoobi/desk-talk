"""
The live view and the proof photos:

    GET /api/video_feed[/<camera>]        live video with the boxes drawn (MJPEG); nothing at all is sent, not even
    GET /api/video_feed_raw[/<camera>]    headers, until there's a first picture. _raw: the plain picture
    GET /api/latest_frame[_raw]/<camera>  the newest picture, once (404 before the first)
    GET /api/snapshot/<id>                one proof photo (404 if it's gone)
    GET /api/snapshots                    the proof photos kept, newest first
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from desktalk.bootstrap import DeskTalk
from desktalk.web.deps import Desk, Streams, camera_index
from desktalk.web.streams import MJPEG_TYPE, NO_CACHE, VIDEO_HEADERS, LiveStreams

JPEG = 'image/jpeg'

router = APIRouter()


def with_boxes(desk: DeskTalk, camera: int) -> Callable[[], bytes | None]:
    def latest() -> bytes | None:
        with desk.desk.lock:
            return desk.desk.frames.get(camera)
    return latest


def plain(desk: DeskTalk, camera: int) -> Callable[[], bytes | None]:
    def latest() -> bytes | None:
        with desk.desk.lock:
            return desk.desk.raw_frames.get(camera) or desk.desk.frames.get(camera)
    return latest


async def video(request: Request, desk: DeskTalk, streams: LiveStreams, latest: Callable[[], bytes | None]) -> Response:
    web = desk.config.web
    first = await streams.first_picture(latest, request, web.video_check_every_sec)
    if first is None:              # the page went away (or the server is stopping) before there was a picture
        return Response(status_code=204)
    body = streams.pictures(latest, first, desk.clock, resend_sec=web.video_resend_sec,
                            check_every_sec=web.video_check_every_sec)
    return StreamingResponse(body, media_type=MJPEG_TYPE, headers=VIDEO_HEADERS)


@router.get('/api/video_feed')
async def video_feed(request: Request, desk: Desk, streams: Streams) -> Response:
    return await video(request, desk, streams, with_boxes(desk, camera_index(desk)))


@router.get('/api/video_feed/{camera:int}')
async def video_feed_of(camera: int, request: Request, desk: Desk, streams: Streams) -> Response:
    return await video(request, desk, streams, with_boxes(desk, camera))


@router.get('/api/video_feed_raw')
async def video_feed_raw(request: Request, desk: Desk, streams: Streams) -> Response:
    return await video(request, desk, streams, plain(desk, camera_index(desk)))


@router.get('/api/video_feed_raw/{camera:int}')
async def video_feed_raw_of(camera: int, request: Request, desk: Desk, streams: Streams) -> Response:
    return await video(request, desk, streams, plain(desk, camera))


def picture(jpeg: bytes | None) -> Response:
    return Response(bytes(jpeg), media_type=JPEG, headers=NO_CACHE) if jpeg else Response(status_code=404)


@router.get('/api/latest_frame/{camera:int}')
def latest_frame(camera: int, desk: Desk) -> Response:
    with desk.desk.lock:
        return picture(desk.desk.frames.get(camera) or desk.desk.raw_frames.get(camera))


@router.get('/api/latest_frame_raw/{camera:int}')
def latest_frame_raw(camera: int, desk: Desk) -> Response:
    return picture(plain(desk, camera)())


@router.get('/api/snapshot/{photo_id}')
def snapshot(photo_id: str, desk: Desk) -> Response:
    with desk.desk.lock:
        jpeg = desk.desk.snapshots.get(photo_id)
    if not jpeg:
        return Response(status_code=404)
    keep_for = desk.config.web.photo_cache_sec
    return Response(jpeg, media_type=JPEG, headers={'Cache-Control': f'public, max-age={keep_for}'})


@router.get('/api/snapshots')
def snapshots(desk: Desk) -> dict[str, Any]:
    with desk.desk.lock:
        return {'items': [dict(item) for item in desk.desk.snapshot_order]}
