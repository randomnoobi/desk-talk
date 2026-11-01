"""
The cloud server's website (cloud/web/) in a headless Chromium: the real pages and routes on a real server, with a fake
Google and the in-memory store.

    uv run playwright install chromium      # once
    uv run pytest -m e2e tests/e2e/test_cloud_site.py

The page must be built (cloud/web/dist/); the `cloud_site_folder` fixture builds it when it isn't.
"""

from __future__ import annotations

import contextlib
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles

from desktalk_cloud import bootstrap
from desktalk_cloud.app import CloudParts
from desktalk_cloud.clock import SystemClock
from desktalk_cloud.domain import DONE, GoogleUser
from tests.fakes.cloud import CLOUD_FOLDER, FakeGoogleLogin, cloud_config, make_parts

pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect  # noqa: E402  (only when Playwright is installed)

pytestmark = pytest.mark.e2e

THIS_COMPUTER = '127.0.0.1'
WEB = CLOUD_FOLDER / 'web'
BUILD_WAIT_SEC = 600
START_WAIT_SEC = 10
GOOGLE_CODE = 'code-from-google'


class GoogleThatSendsYouStraightBack(FakeGoogleLogin):
    """Instead of a Google page, the browser goes straight back to the server, as if the person had said yes."""

    def login_url(self, state: str, redirect_uri: str) -> str:
        return f'{redirect_uri}?code={GOOGLE_CODE}&state={state}'


@pytest.fixture(scope='module')
def cloud_site_folder() -> Path:
    built = WEB / 'dist'
    if (built / 'index.html').is_file():
        return built
    npm = shutil.which('npm')
    if npm is None:
        pytest.skip("The website isn't built (cloud/web/dist/) and npm isn't installed to build it.")
    if not (WEB / 'node_modules').is_dir():
        subprocess.run([npm, 'ci'], cwd=WEB, check=True, capture_output=True, timeout=BUILD_WAIT_SEC)
    subprocess.run([npm, 'run', 'build'], cwd=WEB, check=True, capture_output=True, timeout=BUILD_WAIT_SEC)
    return built


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((THIS_COMPUTER, 0))
        return probe.getsockname()[1]


@pytest.fixture
def site(cloud_site_folder: Path) -> Iterator[tuple[str, CloudParts]]:
    port = free_port()
    url = f'http://{THIS_COMPUTER}:{port}'
    google = GoogleThatSendsYouStraightBack()
    google.people[GOOGLE_CODE] = GoogleUser(sub='google-ann', email='ann@example.com', name='Ann', email_verified=True)
    parts = make_parts(cloud_config(server={'public_url': url}), clock=SystemClock(), google=google)
    app = bootstrap.build_app(parts)
    app.mount('/', StaticFiles(directory=cloud_site_folder, html=True))     # nginx does this in the real stack
    server = uvicorn.Server(uvicorn.Config(app, host=THIS_COMPUTER, port=port, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + START_WAIT_SEC
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield url, parts
    finally:
        server.should_exit = True
        with contextlib.suppress(RuntimeError):
            thread.join(START_WAIT_SEC)


def test_log_in_add_a_pi_see_it_listed_and_read_its_report(page: Any, site: tuple[str, CloudParts]):
    url, parts = site
    page.goto(url)

    page.get_by_role('link', name='Log in with Google').click()
    add = page.get_by_role('button', name='Add a Pi')
    expect(add).to_be_visible()
    expect(page.get_by_text('No Pis linked yet.')).to_be_visible()

    with page.expect_response('**/api/devices/code') as made:
        add.click()
    assert made.value.status == 200
    code = made.value.json()['code']
    expect(page.get_by_text(code, exact=True)).to_be_visible()

    # What the setup command does on the Pi.
    claimed = httpx.post(f'{url}/v1/devices/claim', json={'code': code, 'name': 'Desk Pi'})
    assert claimed.status_code == 200
    page.reload()
    expect(page.get_by_role('link', name='Desk Pi')).to_be_visible()

    # What the report job does in the real stack.
    now = parts.clock.now()
    report = parts.store.create_report(claimed.json()['device_id'], '2026-10-19', now)
    assert report is not None
    parts.store.finish_report(report.id, DONE, '# A good week\n\nThe water bottle came out often.', 'fake', None, now)

    page.get_by_role('link', name='Reports').click()
    page.get_by_role('link', name='Week of 2026-10-19').click()
    expect(page.get_by_role('heading', name='A good week')).to_be_visible()
    expect(page.get_by_text('The water bottle came out often.')).to_be_visible()
