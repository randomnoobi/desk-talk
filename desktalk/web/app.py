"""
The two web servers, built on the running app (desktalk/bootstrap.py → DeskTalk):

    main = create_app(desktalk, links)      # the app in your browser (web_app/) and the Pi: web.port
    phone = create_phone_app(desktalk)      # the phone camera page's own HTTPS server: camera.phone_port

The routes are in desktalk/web/routers/, one module per area. desktalk/web/serve.py runs the servers.

The main server's lifespan runs the background jobs: desktalk.start() when it starts, desktalk.stop() when it
stops. (FastAPI's TestClient without `with`, or uvicorn with lifespan off, leaves them off: tests drive the
desk by hand.)

The replies are as the Flask server gave them (tests/characterization/ checks each one):
- errors are {"error": "..."} with the same status codes, never {"detail": ...}; a body that doesn't fit is a
  400 (not 422), and a request bigger than web.max_request_mb a 413;
- every main-server reply has Access-Control-Allow-Origin: *; the phone server's don't;
- /api/live/ (a trailing slash) is a 404, not a redirect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from desktalk.bootstrap import DeskTalk
from desktalk.web.files import PageFiles, page
from desktalk.web.middleware import MB, AnyPageMayAsk, LogFailedRequests, SizeLimit
from desktalk.web.pairing import Links
from desktalk.web.routers import MAIN_SERVER, PHONE_SERVER
from desktalk.web.streams import LiveStreams

SOMETHING_WENT_WRONG = 'Something went wrong on this computer. The details are in the log.'


@asynccontextmanager
async def run_background_jobs(app: FastAPI) -> AsyncIterator[None]:
    desktalk: DeskTalk = app.state.desktalk
    desktalk.start()
    try:
        yield
    finally:
        await anyio.to_thread.run_sync(desktalk.stop)       # waits for the jobs' threads


def create_app(desktalk: DeskTalk, links: Links) -> FastAPI:
    """The main server: the app's page and everything it asks for, and the Pi's uploads."""
    app = _new_app(desktalk, 'main', lifespan=run_background_jobs)
    app.state.links = links
    app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
    app.add_middleware(AnyPageMayAsk)
    for router in MAIN_SERVER:
        app.include_router(router)
    pages = desktalk.config.resolve(desktalk.config.web.app_folder)

    @app.get('/')
    def index() -> FileResponse:
        return page(pages / 'index.html')

    app.mount('/', PageFiles(directory=pages), name='web_app')     # last: every other path is a file
    return app


def create_phone_app(desktalk: DeskTalk) -> FastAPI:
    """The phone camera page's own server (phones only allow the camera on https:// pages)."""
    app = _new_app(desktalk, 'phone')
    for router in PHONE_SERVER:
        app.include_router(router)
    config = desktalk.config
    app.mount('/phone', PageFiles(directory=config.resolve(config.web.phone_folder)), name='phone_camera')
    shared = config.resolve(config.web.app_folder)
    for folder in ('art', 'fonts'):                  # the drawings and fonts the phone page shares with the app
        app.mount(f'/{folder}', PageFiles(directory=shared / folder), name=folder)
    return app


def _new_app(desktalk: DeskTalk, name: str,
             lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None) -> FastAPI:
    app = FastAPI(title=f'Desk Talk ({name} server)', docs_url=None, redoc_url=None, openapi_url=None,
                  redirect_slashes=False, lifespan=lifespan)
    app.state.desktalk = desktalk
    app.state.streams = LiveStreams()
    app.add_middleware(SizeLimit, limit_bytes=int(desktalk.config.web.max_request_mb * MB))
    app.add_middleware(LogFailedRequests, server=name)
    app.add_exception_handler(HTTPException, _error_reply)
    app.add_exception_handler(RequestValidationError, _bad_request)
    app.add_exception_handler(Exception, _crashed)
    return app


async def _error_reply(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({'error': str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)


async def _bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    """400 with what's wrong, e.g. "by_chat: Input should be a valid dictionary"."""
    problems = []
    for error in exc.errors():
        where = '.'.join(str(part) for part in error.get('loc', ())[1:])      # [0] is body / query / path
        problems.append(f'{where}: {error.get("msg", "not valid")}' if where else str(error.get('msg', 'not valid')))
    return JSONResponse({'error': '; '.join(problems) or 'This request is not valid.'}, status_code=400)


async def _crashed(request: Request, exc: Exception) -> JSONResponse:
    """A route crashed: uvicorn logs it with the details; the page gets a plain {"error"} like any other."""
    return JSONResponse({'error': SOMETHING_WENT_WRONG}, status_code=500)
