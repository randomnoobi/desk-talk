"""
The two web servers, built on the running app (desktalk/bootstrap.py → DeskTalk):

    main = create_app(desktalk, links)      # the app in your browser (web_app/) and the Pi: web.port, plus
                                            # the developer page at /dev/ (dev_ui/) when dev.enabled is true
    phone = create_phone_app(desktalk)      # the phone camera page's own HTTPS server: camera.phone_port

The routes are in desktalk/web/routers/, one module per area. desktalk/web/serve.py runs the servers.

The main server's lifespan runs the background jobs: desktalk.start() when it starts, desktalk.stop() when it
stops. (FastAPI's TestClient without `with`, or uvicorn with lifespan off, leaves them off: tests drive the
desk by hand.)

The replies are as the Flask server gave them (tests/characterization/ checks each one):
- errors are {"error": "..."} with the same status codes, never {"detail": ...}; a body that doesn't fit is a
  400 (not 422), and a request bigger than web.max_request_mb a 413;
- no other web site may read a reply or send a write, unless it is listed in web.cors_origins (none are);
- a request sent to a name this server doesn't go by is a 403 (KnownNamesOnly), so a site that points its own name
  at this server's address gets nothing;
- /api/live/ (a trailing slash) is a 404, not a redirect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException

from desktalk.bootstrap import DeskTalk
from desktalk.web.files import BuiltPage, PageFiles, page
from desktalk.web.middleware import MB, KnownNamesOnly, LogFailedRequests, OwnPagesOnly, SizeLimit
from desktalk.web.pairing import Links
from desktalk.web.routers import DEV_SERVER, MAIN_SERVER, PHONE_SERVER
from desktalk.web.streams import LiveStreams


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
    app.add_middleware(CORSMiddleware, allow_origins=desktalk.config.web.cors_origins, allow_methods=['*'],
                       allow_headers=['*'])
    for router in MAIN_SERVER:
        app.include_router(router)
    config = desktalk.config
    pages = config.resolve(config.web.app_folder)

    @app.get('/')
    def index() -> FileResponse:
        return page(pages / 'index.html')

    if config.dev.enabled:                          # the developer page: its routes, and the page at /dev/
        for router in DEV_SERVER:
            app.include_router(router)

        @app.get('/dev')
        def dev_page() -> RedirectResponse:         # its files are found from /dev/, with the slash
            return RedirectResponse('/dev/')

        app.mount('/dev', BuiltPage(config.resolve(config.dev.ui_folder), not_built=config.prompts.dev.not_built),
                  name='dev_ui')
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
    config = desktalk.config
    app.add_middleware(OwnPagesOnly, allowed=config.web.cors_origins, refusal=config.prompts.dev.other_site)
    public_name = config.web.public_address.partition(':')[0]
    app.add_middleware(KnownNamesOnly, names=[public_name, *config.web.allowed_hosts],
                       refusal=config.prompts.dev.other_name)
    app.add_middleware(SizeLimit, limit_bytes=int(config.web.max_request_mb * MB))
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
    desktalk: DeskTalk = request.app.state.desktalk
    return JSONResponse({'error': desktalk.config.prompts.server.something_went_wrong}, status_code=500)
