"""
The web servers for the web tests (tests/integration/test_web_*.py), on the whole app from app_kit.DeskRig:

    rig = DeskRig(tmp_path)
    client = web_client(rig.app)                  # FastAPI's TestClient: requests come from this computer
    client = web_client(rig.app, from_ip=ON_THE_WIFI)
    with running(create_app(rig.app, links_for(rig.app))) as url:   # a real server on 127.0.0.1, any free port
        ...
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from desktalk.bootstrap import DeskTalk
from desktalk.domain import UploadInfo
from desktalk.web.app import create_app
from desktalk.web.pairing import Links
from desktalk.web.serve import Lifespan, ServerThread, server_config
from desktalk_shared.wire import encode_upload
from tests.integration.app_kit import PAIR_KEY

THIS_COMPUTER = '127.0.0.1'
ON_THE_WIFI = '192.168.1.50'
ADDRESS = '192.168.1.20'          # this computer on the Wi-Fi, as the Pi command and the phone link show it
CLIENT_PORT = 50000
TEST_ADDRESS = 'http://testserver'      # where FastAPI's TestClient sends requests unless told otherwise


def links_for(app: DeskTalk, *, phone_page: bool = False) -> Links:
    return Links(ADDRESS, app.config.web.port, app.pair_key,
                 phone_port=app.config.camera.phone_port if phone_page else None)


def web_client(app: DeskTalk | FastAPI, *, from_ip: str = THIS_COMPUTER) -> TestClient:
    """Requests to the main server, sent to http://localhost:<web.port> as this computer's browser sends them
    (or to `app` when it's a web app already, at FastAPI's TestClient's own address). The lifespan doesn't run,
    so the background jobs stay off unless the test starts them."""
    if isinstance(app, FastAPI):
        web, address = app, TEST_ADDRESS
    else:
        web, address = create_app(app, links_for(app)), f'http://localhost:{app.config.web.port}'
    return TestClient(web, base_url=address, client=(from_ip, CLIENT_PORT), raise_server_exceptions=False,
                      follow_redirects=False)


def pi_upload(client: TestClient, jpeg: bytes, info: UploadInfo, key: str = PAIR_KEY) -> Any:
    """What the camera program sends: upload format 2."""
    body, content_type = encode_upload(jpeg, info)
    return client.post('/api/pi/frame', content=body, headers={'Content-Type': content_type, 'X-Desk-Key': key})


@contextlib.contextmanager
def running(app: FastAPI, *, lifespan: Lifespan = 'off', **kw: Any) -> Iterator[str]:
    """A real uvicorn server for `app` on 127.0.0.1 and a free port, in a thread; stopped afterwards.
    Yields its address, e.g. http://127.0.0.1:53124. lifespan='on' starts and stops the background jobs."""
    server = ServerThread(server_config(app, host=THIS_COMPUTER, port=0, lifespan=lifespan, **kw), name='test')
    server.start()
    try:
        yield f'{"https" if kw.get("cert") else "http"}://{THIS_COMPUTER}:{server.port}'
    finally:
        server.stop()
