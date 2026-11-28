"""
Running the web server with uvicorn (server.py calls run()):

    run(desktalk)                       the main server until Ctrl+C

When the server stops it first ends its open live streams (chat messages, video), so an open page never holds
it up; then the server's lifespan stops the background jobs (desktalk/web/app.py). Requests still
running get web.stop_wait_sec to finish.

uvicorn logs through the same handlers as the rest of the app (settings → logging), and only failed
requests are logged (desktalk/web/middleware.py).
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

import uvicorn
from fastapi import FastAPI

from desktalk.bootstrap import DeskTalk
from desktalk.web.app import create_app

log = logging.getLogger(__name__)

Lifespan = Literal['auto', 'on', 'off']


def server_config(app: FastAPI, *, host: str, port: int, lifespan: Lifespan = 'auto') -> uvicorn.Config:
    """uvicorn's settings for `app` (built by create_app). port 0 = any free port."""
    desktalk: DeskTalk = app.state.desktalk
    return uvicorn.Config(app, host=host, port=port, lifespan=lifespan, log_config=None, access_log=False,
                          timeout_graceful_shutdown=desktalk.config.web.stop_wait_sec)


class Server(uvicorn.Server):
    """uvicorn's server, which ends the app's open live streams before it waits for requests to finish."""

    def __init__(self, config: uvicorn.Config):
        super().__init__(config)
        self.app: FastAPI = config.app
        self.settled = threading.Event()       # it started, or it gave up trying

    async def startup(self, sockets=None) -> None:
        try:
            await super().startup(sockets)
        finally:
            self.settled.set()

    async def shutdown(self, sockets=None) -> None:
        self.app.state.streams.close_all()
        await super().shutdown(sockets)

    @property
    def port(self) -> int:
        """The port it listens on (the one it picked, for port 0)."""
        return self.servers[0].sockets[0].getsockname()[1]


class ServerThread:
    """A server in a thread of its own: the tests' servers."""

    def __init__(self, config: uvicorn.Config, name: str):
        self.server = Server(config)
        self.name = name
        self._thread = threading.Thread(target=self._run, name=f'{name} web server', daemon=True)

    def _run(self) -> None:
        try:
            self.server.run()
        finally:
            self.server.settled.set()

    def start(self) -> None:
        """Start it and wait until it listens. Raises RuntimeError if it can't (e.g. the port is in use)."""
        self._thread.start()
        self.server.settled.wait()
        if not self.server.started:
            raise RuntimeError(f'The {self.name} web server could not start (the reason is in the log above)')

    @property
    def port(self) -> int:
        return self.server.port

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join()


def run(desktalk: DeskTalk) -> None:
    """Serve until Ctrl+C."""
    web = desktalk.config.web
    Server(server_config(create_app(desktalk), host=web.host, port=web.port, lifespan='on')).run()
