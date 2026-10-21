"""
The two replies that never end:

    chat messages   GET /api/events, Server-Sent Events: "retry: 2000", then one "data: {json}" line per message
                    from the MessageHub, and a ": keepalive" comment when nothing came for web.live_keepalive_sec
    live video      GET /api/video_feed..., MJPEG: one JPEG part after another, sent when the picture changes
                    (and again every web.video_resend_sec, so a closed tab gets noticed)

LiveStreams keeps track of the open ones, so close_all() can end them all when the server stops: an open
page never holds the server up. A page's stream also ends when the hub drops it for not keeping up (CLOSED);
the browser then connects again by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import threading
from collections.abc import AsyncIterator, Callable
from functools import partial

import anyio
from starlette.requests import Request

from desktalk.core.chat.message_hub import CLOSED, MessageHub
from desktalk.ports import Clock

SSE_TYPE = 'text/event-stream; charset=utf-8'
SSE_HEADERS = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
BOUNDARY = 'frame'
MJPEG_TYPE = f'multipart/x-mixed-replace; boundary={BOUNDARY}'
NO_CACHE = {'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0', 'Pragma': 'no-cache', 'Expires': '0'}
VIDEO_HEADERS = {**NO_CACHE, 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'}
KEEPALIVE = ': keepalive\n\n'


class _Stop:
    def __repr__(self) -> str:
        return 'STOP'


STOP = _Stop()      # put in a page's queue here, once the hub has let it go: wakes the stream so it ends


def mjpeg_part(jpeg: bytes) -> bytes:
    return (f'--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n'.encode('ascii')
            + jpeg + b'\r\n')


def _let_go(hub: MessageHub, q: queue.Queue) -> None:
    """Unsubscribe the page, then wake its stream if it's waiting (the hub puts nothing more in the queue)."""
    hub.unsubscribe(q)
    with contextlib.suppress(queue.Full):     # full: it isn't waiting, and it checks for the stop after each message
        q.put_nowait(STOP)


class LiveStreams:
    def __init__(self):
        self._lock = threading.Lock()
        self._open: dict[queue.Queue, MessageHub] = {}
        self._closing = threading.Event()

    @property
    def closing(self) -> bool:
        return self._closing.is_set()

    def close_all(self) -> None:
        """End every open stream and any opened later (the server is stopping)."""
        self._closing.set()
        with self._lock:
            pages = list(self._open.items())
        for q, hub in pages:
            _let_go(hub, q)

    async def chat_messages(self, hub: MessageHub, *, retry_ms: int, keepalive_sec: float) -> AsyncIterator[str]:
        """The SSE body for one open page."""
        q = hub.subscribe()
        with self._lock:
            self._open[q] = hub
        try:
            yield f'retry: {retry_ms}\n\n'
            while not self.closing:
                try:
                    # In a thread: queue.get blocks. Abandoned when the page hangs up; _let_go below wakes it.
                    msg = await anyio.to_thread.run_sync(partial(q.get, timeout=keepalive_sec), abandon_on_cancel=True)
                except queue.Empty:
                    yield KEEPALIVE
                    continue
                if msg is CLOSED or msg is STOP:
                    return
                yield f'data: {json.dumps(msg, default=str)}\n\n'
        finally:
            with self._lock:
                self._open.pop(q, None)
            _let_go(hub, q)

    async def first_picture(self, latest: Callable[[], bytes | None], request: Request,
                            check_every_sec: float) -> bytes | None:
        """Wait, sending nothing at all, until there's a picture. None if the page went away or the server is
        stopping first."""
        while (jpeg := latest()) is None:
            if self.closing or await request.is_disconnected():
                return None
            await asyncio.sleep(check_every_sec)
        return jpeg

    async def pictures(self, latest: Callable[[], bytes | None], first: bytes, clock: Clock, *, resend_sec: float,
                       check_every_sec: float) -> AsyncIterator[bytes]:
        """The MJPEG body: `first`, then each new picture (each new one is a new bytes object, so `is not`
        tells that it changed), and the same one again after resend_sec."""
        last, sent_at = first, clock.monotonic()
        yield mjpeg_part(first)
        while not self.closing:
            await asyncio.sleep(check_every_sec)
            jpeg, now = latest(), clock.monotonic()
            if jpeg and (jpeg is not last or now - sent_at > resend_sec):
                yield mjpeg_part(jpeg)
                last, sent_at = jpeg, now
