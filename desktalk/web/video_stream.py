"""Live video for the app: a multipart JPEG (MJPEG) stream."""

from __future__ import annotations

import time

from flask import Response, stream_with_context

BOUNDARY = b'frame'
MIMETYPE = 'multipart/x-mixed-replace; boundary=frame'
HEADERS = {
    'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0',
    'Pragma': 'no-cache',
    'Expires': '0',
    'X-Accel-Buffering': 'no',
    'Connection': 'keep-alive',
}
NO_CACHE = {'Cache-Control': 'no-cache, no-store, must-revalidate, max-age=0', 'Pragma': 'no-cache', 'Expires': '0'}


def chunk(jpeg_bytes: bytes) -> bytes:
    n = str(len(jpeg_bytes)).encode('ascii')
    return (b'--' + BOUNDARY + b'\r\n' b'Content-Type: image/jpeg\r\n'
            b'Content-Length: ' + n + b'\r\n' b'\r\n' + jpeg_bytes + b'\r\n')


def mjpeg_response(frame_getter) -> Response:
    """Stream JPEGs; frame_getter returns the newest JPEG bytes or None.

    Only sends a frame when it's new (each new frame is a new bytes object, so
    `is not` tells us it changed). A repeat every 2 s keeps the connection alive
    so closed tabs get noticed."""

    def generate():
        last, last_sent = None, 0.0
        while True:
            try:
                jpeg = frame_getter()
                now = time.time()
                if jpeg and (jpeg is not last or now - last_sent > 2.0):
                    yield chunk(jpeg)
                    last, last_sent = jpeg, now
            except Exception as exc:
                print(f'  [mjpeg] stream chunk error: {exc}')
            time.sleep(0.03)

    return Response(stream_with_context(generate()), mimetype=MIMETYPE, headers=dict(HEADERS),
                    direct_passthrough=True)
