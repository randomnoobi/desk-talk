"""The two replies that never end: chat messages over SSE (/api/events) and the live video (MJPEG).

Each test reads only the start of the stream, then hangs up.
"""

from __future__ import annotations

import json
import threading
import time

import cv2
import numpy as np
import pytest

from .cases import MJPEG, MJPEG_WAITS, SSE_MESSAGE_SNAPSHOT, case, cases_of
from .shapes import check_snapshot, shape_of
from .test_replies import observed

PICTURE_SIZE = (480, 640, 3)          # the pictures the desks are fed (conftest.blank_frame)
QUIET_SEC = 0.5                       # how long the video must stay silent while there's no picture


def sse_events(body: bytes) -> list[bytes]:
    """Complete SSE events (each one ends with a blank line)."""
    return body.split(b'\n\n')[:-1]


def first_picture(body: bytes) -> tuple[str, dict, bytes] | None:
    """The first part of an MJPEG body: (boundary, its headers, its bytes), or None if it isn't all here yet."""
    line_end = body.find(b'\r\n')
    if not body.startswith(b'--') or line_end < 0:
        return None
    head_end = body.find(b'\r\n\r\n', line_end)
    if head_end < 0:
        return None
    headers = {}
    for line in body[line_end + 2:head_end].decode('latin-1').split('\r\n'):
        name, _, value = line.partition(':')
        headers[name.strip().lower()] = value.strip()
    size = int(headers.get('content-length', -1))
    start = head_end + 4
    if size < 0 or len(body) < start + size:
        return None
    return body[2:line_end].decode('latin-1'), headers, body[start:start + size]


def boundary_of(content_type: str) -> str:
    kind, _, rest = content_type.partition(';')
    assert kind.strip() == 'multipart/x-mixed-replace', content_type
    params = dict(p.strip().split('=', 1) for p in rest.split(';') if '=' in p)
    return params['boundary']


def test_sse_sends_retry_then_chat_messages(desks):
    events = case('events')
    desk = desks(events.desk)
    chat = {}

    def say_hello():
        chat['reply'] = desk.main.post('/api/chat', json={'chat': 'group', 'message': '@Computer hello there'})

    reply = desk.main.stream(events.path, after_open=say_hello, until=lambda body: len(sse_events(body)) >= 2)
    assert reply.status == events.status
    check_snapshot(events.id, observed(events, reply))
    first, second = sse_events(reply.body)[:2]
    assert first == b'retry: 2000'                       # the browser reconnects after 2 s
    assert second.startswith(b'data: ') and b'\n' not in second, second[:200]
    message = json.loads(second[len(b'data: '):])
    check_snapshot(SSE_MESSAGE_SNAPSHOT, {'json': shape_of(message)})
    # The same message is in the chat reply too, with the same id, so the app shows it only once.
    assert chat['reply'].status == 200
    assert message['id'] == chat['reply'].json()['messages'][0]['id']


@pytest.mark.parametrize('video', cases_of(MJPEG), ids=lambda c: c.id)
def test_video_stream_starts_with_a_jpeg(video, desks):
    reply = desks(video.desk).main.stream(video.path, until=lambda body: first_picture(body) is not None)
    assert reply.status == video.status
    check_snapshot(video.id, observed(video, reply))
    part = first_picture(reply.body)
    assert part is not None, f'no complete picture in the first {len(reply.body)} bytes'
    boundary, headers, jpeg = part
    assert boundary == boundary_of(reply.content_type)
    assert headers.get('content-type') == 'image/jpeg'
    picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert picture is not None, f'the first part ({len(jpeg)} bytes) is not a JPEG picture'
    assert picture.shape == PICTURE_SIZE


@pytest.mark.parametrize('video', cases_of(MJPEG_WAITS), ids=lambda c: c.id)
def test_video_stream_waits_for_the_first_picture(video, desks):
    desk = desks(video.desk)            # no pictures yet
    camera_on = threading.Event()
    before_camera = []

    def until(body: bytes) -> bool:
        if not camera_on.is_set():
            before_camera.append(body)
        return first_picture(body) is not None

    def camera_starts_late():
        time.sleep(QUIET_SEC)
        camera_on.set()
        desk.watch(1)

    reply = desk.main.stream(video.path, until=until, meanwhile=camera_starts_late)
    assert not before_camera, 'the video sent something before there was a picture'
    assert reply.status == video.status
    check_snapshot(video.id, observed(video, reply))
    part = first_picture(reply.body)
    assert part is not None and part[0] == boundary_of(reply.content_type)
