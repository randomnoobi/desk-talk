"""
The detector service's web API. The shapes are in desktalk_shared.detector_api, and every reply is
written with their to_dict(), so the JSON is exactly what the camera program reads.

    POST /detect   one JPEG              → DetectReply   (400 not a JPEG, 413 too big, 429 busy with another
                                                          picture, 503 no model yet)
    GET  /health                         → DetectorHealth
    PUT  /model    ModelSettings as JSON → DetectorHealth (400 bad settings); the model loads in the background

Errors reply {"detail": "<what's wrong, in plain words>"}, like FastAPI's own errors.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from desktalk_detector.manager import DetectorBusy, ModelManager
from desktalk_detector.settings import BYTES_PER_MB
from desktalk_shared.detection import DetectorUnavailable
from desktalk_shared.detector_api import ModelSettings

log = logging.getLogger(__name__)


def _problem(status: int, text: str) -> JSONResponse:
    return JSONResponse({'detail': text}, status_code=status)


def create_app(manager: ModelManager, *, max_picture_bytes: int) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        manager.start()             # loads the model asked for last time, then waits for PUT /model
        yield
        manager.stop()

    app = FastAPI(title='Desk Talk detector', lifespan=lifespan)

    def detect(jpeg: bytes) -> JSONResponse:
        picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) if jpeg else None
        if picture is None:
            return _problem(400, 'The body must be one JPEG picture.')
        try:
            reply = manager.detect(picture)
        except DetectorBusy as exc:
            return _problem(429, str(exc))
        except DetectorUnavailable as exc:
            return _problem(503, str(exc))
        except Exception as exc:     # whatever the model raised: say so, and keep serving
            log.exception('detection failed')
            return _problem(500, f'Detection failed: {exc}')
        return JSONResponse(reply.to_dict())

    @app.post('/detect')
    async def post_detect(request: Request) -> JSONResponse:
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > max_picture_bytes:
                return _problem(413, f'The picture is bigger than {max_picture_bytes / BYTES_PER_MB:g} MB '
                                     '(DETECTOR_MAX_PICTURE_MB).')
        return await run_in_threadpool(detect, bytes(body))     # decoding and YOLO take a while: not here

    @app.get('/health')
    def get_health() -> JSONResponse:
        return JSONResponse(manager.health().to_dict())

    @app.put('/model')
    async def put_model(request: Request) -> JSONResponse:
        try:
            data = json.loads(await request.body())
        except ValueError:
            return _problem(400, 'The body must be the model settings as JSON.')
        try:
            health = manager.want(ModelSettings.from_dict(data))
        except ValueError as exc:
            return _problem(400, str(exc))
        return JSONResponse(health.to_dict())

    return app
