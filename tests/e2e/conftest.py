"""
A headless Chromium for the browser tests. They're skipped (not failed) when Chromium isn't installed:

    uv run playwright install chromium      # once
    uv run pytest -m e2e tests/e2e

The developer page's test (test_dev_page.py) needs the page built (dev_ui/dist/). When it isn't, `dev_ui_folder`
builds it with npm first, and skips the test when npm isn't installed.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.app_kit import ROOT

WAIT_MS = 10_000          # longest a step waits for the page by default (a test may wait less)
DEV_UI = ROOT / 'dev_ui'
BUILD_WAIT_SEC = 600      # npm ci (the first time) and npm run build, each
SHOW_LAST_CHARS = 3000    # how much of npm's printout a failed build shows


@pytest.fixture(scope='package')
def browser() -> Iterator[Any]:
    """One browser for the tests in this folder. It closes when they're done (not at the end of the whole run),
    because Playwright keeps an event loop running while it's open, and the stream tests in tests/integration
    start their own."""
    sync_api = pytest.importorskip('playwright.sync_api')
    with sync_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"Chromium isn't installed for Playwright (run: uv run playwright install chromium): {exc}")
        try:
            yield chromium
        finally:
            chromium.close()


@pytest.fixture
def page(browser: Any) -> Iterator[Any]:
    """A fresh browser tab with nothing saved from earlier tests."""
    context = browser.new_context()
    context.set_default_timeout(WAIT_MS)
    tab = context.new_page()
    try:
        yield tab
    finally:
        context.close()


def _npm(npm: str, *args: str) -> None:
    done = subprocess.run([npm, *args], cwd=DEV_UI, capture_output=True, text=True, timeout=BUILD_WAIT_SEC,
                          check=False)
    if done.returncode != 0:
        said = (done.stdout + done.stderr)[-SHOW_LAST_CHARS:]
        raise AssertionError(f'npm {" ".join(args)} in dev_ui/ ended with {done.returncode}:\n{said}')


@pytest.fixture(scope='package')
def dev_ui_folder() -> Path:
    """The built developer page (dev_ui/dist/). Built here first when it isn't there yet; the test is skipped
    when it isn't built and npm isn't installed."""
    built = DEV_UI / 'dist'
    if (built / 'index.html').is_file():
        return built
    npm = shutil.which('npm')
    if npm is None:
        pytest.skip("The developer page isn't built (dev_ui/dist/) and npm isn't installed to build it. "
                    'Install Node 22, then run `npm ci && npm run build` in dev_ui/.')
    if not (DEV_UI / 'node_modules').is_dir():
        _npm(npm, 'ci')
    _npm(npm, 'run', 'build')
    return built
