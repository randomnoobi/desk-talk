"""
The live view and the proof photos:

    GET /api/video_feed/<camera>          live video with the boxes drawn (MJPEG); nothing at all is sent, not even
    GET /api/video_feed_raw[/<camera>]    headers, until there's a first picture. _raw: the plain picture
    GET /api/latest_frame_raw/<camera>    the newest plain picture, once (404 before the first)
    GET /api/snapshot/<id>                one proof photo (404 if it's gone)
    GET /api/snapshots                    the proof photos kept, newest first
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from desktalk.bootstrap import DeskTalk
from desktalk.web.deps import App, Streams, camera_index
from desktalk.web.streams import MJPEG_TYPE, NO_CACHE, VIDEO_HEADERS, LiveStreams

JPEG = 'image/jpeg'

router = APIRouter()


def with_boxes(app: DeskTalk, camera: int) -> Callable[[], bytes | None]:
    def latest() -> bytes | None:
        return app.live_pictures.boxed(camera)
    return latest


def plain(app: DeskTalk, camera: int) -> Callable[[], bytes | None]:
    """The plain picture, or the one with boxes when there's no plain one."""
    def latest() -> bytes | None:
        return app.live_pictures.plain(camera) or app.live_pictures.boxed(camera)
    return latest


async def video(request: Request, app: DeskTalk, streams: LiveStreams, latest: Callable[[], bytes | None]) -> Response:
    web = app.config.web
    first = await streams.first_picture(latest, request, web.video_check_every_sec)
    if first is None:              # the page went away (or the server is stopping) before there was a picture
        return Response(status_code=204)
    body = streams.pictures(latest, first, app.clock, resend_sec=web.video_resend_sec,
                            check_every_sec=web.video_check_every_sec)
    return StreamingResponse(body, media_type=MJPEG_TYPE, headers=VIDEO_HEADERS)


@router.get('/api/video_feed/{camera:int}')
async def video_feed_of(camera: int, request: Request, app: App, streams: Streams) -> Response:
    return await video(request, app, streams, with_boxes(app, camera))


@router.get('/api/video_feed_raw')
async def video_feed_raw(request: Request, app: App, streams: Streams) -> Response:
    return await video(request, app, streams, plain(app, camera_index(app)))


@router.get('/api/video_feed_raw/{camera:int}')
async def video_feed_raw_of(camera: int, request: Request, app: App, streams: Streams) -> Response:
    return await video(request, app, streams, plain(app, camera))


def picture(jpeg: bytes | None) -> Response:
    return Response(bytes(jpeg), media_type=JPEG, headers=NO_CACHE) if jpeg else Response(status_code=404)


@router.get('/api/latest_frame_raw/{camera:int}')
def latest_frame_raw(camera: int, app: App) -> Response:
    return picture(plain(app, camera)())


@router.get('/api/snapshot/{photo_id}')
def snapshot(photo_id: str, app: App) -> Response:
    photo = app.photo_album.get(photo_id)
    if photo is None or not photo.jpeg:
        return Response(status_code=404)
    keep_for = app.config.web.photo_cache_sec
    return Response(photo.jpeg, media_type=JPEG, headers={'Cache-Control': f'public, max-age={keep_for}'})


@router.get('/api/snapshots')
def snapshots(app: App) -> dict[str, Any]:
    every_photo = app.config.snapshots.max_kept       # the album never keeps more
    return {'items': [photo.to_dict() for photo in app.photo_album.newest(every_photo)]}
