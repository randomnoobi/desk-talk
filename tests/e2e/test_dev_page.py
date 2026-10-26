"""
The developer page (dev_ui/, at /dev/) in a headless Chromium, on the whole app on a real server
(tests/e2e_kit/server.py), with a pretend Pi and a fake detector that finds a cup in picture 2.

    uv run playwright install chromium      # once
    uv run pytest -m e2e tests/e2e/test_dev_page.py

The page must be built (dev_ui/dist/); the `dev_ui_folder` fixture in conftest.py builds it when it isn't.
"""

from __future__ import annotations

import contextlib
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from desktalk.config import AppConfig
from desktalk.config.app import DevCheck
from desktalk.domain import BBox, Detection
from tests.e2e_kit.server import AppServer, desk_pictures
from tests.integration.app_kit import changed
from tests.integration.web_kit import THIS_COMPUTER

pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect  # noqa: E402  (only when Playwright is installed)

pytestmark = pytest.mark.e2e

CUP = Detection('cup', 0.9, BBox(420, 260, 470, 380))
CUP_IN_PICTURE_2 = {2: [CUP]}
# "Run everything" runs these instead of the real tests, lint and types, so it's over in a moment.
QUICK_CHECKS = (
    DevCheck(name='First', command=(sys.executable, '-c', 'print("first")')),
    DevCheck(name='Second', command=(sys.executable, '-c', 'print("second")')),
    DevCheck(name='Third', command=(sys.executable, '-c', 'print("third")')),
)
# The Checks tab lists the tests when it opens: pytest in the test's own (empty) project folder, so it's quick.
LIST_TESTS = (sys.executable, '-m', 'pytest', '-o', 'addopts=', '-q')
RUN_WITHIN_MS = 30_000
TABS = {                  # each tab's name, and something it shows once it has loaded
    'Checks': ('button', 'Run everything'),
    'Live': ('heading', 'Now'),
    'System': ('heading', 'System'),
    'Database': ('heading', 'Databases'),
    'Storage': ('heading', 'In this browser'),
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((THIS_COMPUTER, 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def dev_server(tmp_path: Path, dev_ui_folder: Path) -> Iterator[AppServer]:
    """The app with the developer page on a port picked here and written into settings → web.port: the page's
    routes only answer a POST from the server's own page (its Origin must have that port)."""
    port = free_port()

    def change(config: AppConfig) -> AppConfig:
        config = changed(config, 'web', port=port)
        return changed(config, 'dev', ui_folder=dev_ui_folder, checks=QUICK_CHECKS, test_command=LIST_TESTS)

    server = AppServer(tmp_path, pictures=desk_pictures(tmp_path), found=CUP_IN_PICTURE_2, change=change)
    server.port = port
    server.start()
    try:
        yield server
    finally:
        server.close()


def watch_for_errors(page: Any) -> list[str]:
    errors: list[str] = []
    page.on('pageerror', lambda exc: errors.append(f'page error: {exc}'))
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
    return errors


def open_tab(page: Any, name: str) -> Any:
    page.get_by_role('tab', name=name).click()
    expect(page.get_by_role('tab', name=name)).to_have_attribute('aria-selected', 'true')
    return page.get_by_role('tabpanel', name=name)


def test_every_tab_loads(page, tmp_path, dev_ui_folder):
    errors = watch_for_errors(page)
    with dev_server(tmp_path, dev_ui_folder) as server:
        server.show(1)
        page.goto(f'{server.url}/dev/', wait_until='domcontentloaded')
        for name, (role, shows) in TABS.items():
            panel = open_tab(page, name)
            expect(panel.get_by_role(role, name=shows, exact=True)).to_be_visible()
    assert errors == []


def test_live_tab_shows_the_cup(page, tmp_path, dev_ui_folder):
    with dev_server(tmp_path, dev_ui_folder) as server:
        server.show(1)                                      # the camera is on, with nothing on the desk
        page.goto(f'{server.url}/dev/#live', wait_until='domcontentloaded')
        live = page.get_by_role('tabpanel', name='Live')
        expect(live.get_by_text('No objects seen yet.').first).to_be_visible()
        server.show(2)                                      # now a cup
        rules = server.config
        # After appear_sec + 1 seconds of the cup, it's PRESENT; the page may take poll_sec more to ask again.
        within_ms = (rules.object_states.appear_sec + 1 + rules.dev.poll_sec) * 1000
        cup = live.get_by_role('button', name='cup', exact=True)    # the cup's card is a button that opens its trace
        expect(cup.get_by_text('PRESENT', exact=True)).to_be_visible(timeout=within_ms)


def test_database_tab_lists_events(page, tmp_path, dev_ui_folder):
    with dev_server(tmp_path, dev_ui_folder) as server:
        page.goto(f'{server.url}/dev/#database', wait_until='domcontentloaded')
        database = page.get_by_role('tabpanel', name='Database')
        expect(database.get_by_role('cell', name='events', exact=True)).to_be_visible()


def test_run_everything_passes(page, tmp_path, dev_ui_folder):
    with dev_server(tmp_path, dev_ui_folder) as server:
        page.goto(f'{server.url}/dev/#checks', wait_until='domcontentloaded')
        checks = page.get_by_role('tabpanel', name='Checks')
        steps = checks.get_by_role('list', name='Steps')
        for check in QUICK_CHECKS:
            expect(steps).to_contain_text(f'{check.name}: waiting')
        checks.get_by_role('button', name='Run everything').click()
        for check in QUICK_CHECKS:
            expect(steps.get_by_text(f'{check.name}: passed', exact=True)).to_be_visible(timeout=RUN_WITHIN_MS)
        printout = checks.get_by_label('Printout')
        for check in QUICK_CHECKS:
            expect(printout).to_contain_text(check.name.lower())
        expect(checks.get_by_role('button', name='Run everything')).to_be_enabled()
        expect(checks.get_by_text('passed', exact=True)).to_be_visible()      # the run as a whole
