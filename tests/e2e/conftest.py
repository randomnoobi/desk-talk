"""
A headless Chromium for the browser tests. They're skipped (not failed) when Chromium isn't installed:

    uv run playwright install chromium      # once
    uv run pytest -m e2e tests/e2e
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

WAIT_MS = 10_000          # longest a step waits for the page by default (a test may wait less)


@pytest.fixture(scope='session')
def browser() -> Iterator[Any]:
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
