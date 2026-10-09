#!/usr/bin/env python3
"""
Capture Desk Talk app screens for sitemap / documentation.

Requires: pip install playwright && playwright install chromium

Default: serves ``app/`` with ``python -m http.server`` on port 8765 (no Flask/OpenCV).

With full backend running (``python server.py`` on port 5000) for live API-backed UI::

  python scripts/capture_app_sitemap.py --url http://127.0.0.1:5000

Outputs: ``_screenshots/sitemap/*.png``
"""

from __future__ import annotations

import argparse
import http.client
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "_screenshots" / "sitemap"
STATIC_PORT = int(os.environ.get("SITEMAP_CAPTURE_PORT", "8765"))
AUTO_BASE = f"http://127.0.0.1:{STATIC_PORT}"


def server_responds(port: int, timeout_sec: float = 2.0) -> bool:
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_sec)
        c.request("GET", "/")
        r = c.getresponse()
        r.read()
        c.close()
        return r.status < 500
    except OSError:
        return False


def wait_server(port: int, max_wait: float = 20.0) -> None:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if server_responds(port):
            return
        time.sleep(0.25)
    sys.stderr.write(f"Timed out waiting for http://127.0.0.1:{port}\n")
    sys.exit(1)


def shot(page, filename: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    page.locator(".phone-frame").screenshot(path=str(path))
    print(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="",
        help="App root URL (e.g. http://127.0.0.1:5000). If omitted, auto-starts static server for app/.",
    )
    args = parser.parse_args()

    proc = None
    base = args.url.strip().rstrip("/")

    if not base:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(STATIC_PORT),
                "--bind",
                "127.0.0.1",
            ],
            cwd=str(ROOT / "app"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_server(STATIC_PORT)
        base = AUTO_BASE

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.stderr.write(
            "Install Playwright: pip install playwright && playwright install chromium\n"
        )
        if proc:
            proc.terminate()
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entry = f"{base}/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 560, "height": 980},
            device_scale_factor=2,
        )
        page = context.new_page()
        page.goto(entry, wait_until="domcontentloaded")
        page.wait_for_selector("#screen-splash.active", timeout=15000)

        shot(page, "sitemap-01-splash.png")

        page.click("#splash-start")
        page.wait_for_selector("#screen-chatlist.active", timeout=10000)
        page.wait_for_timeout(400)

        shot(page, "sitemap-02-chat-list.png")

        page.click("#mode-toggle")
        page.wait_for_timeout(300)
        shot(page, "sitemap-02b-chat-list-live-mode-on.png")
        page.click("#mode-toggle")
        page.wait_for_timeout(200)

        page.click('.surface-banner[data-chat="group"]')
        page.wait_for_selector("#screen-chat.active", timeout=5000)
        page.wait_for_timeout(400)
        shot(page, "sitemap-03-chat-group-the-desk.png")
        page.click("#btn-back")
        page.wait_for_selector("#screen-chatlist.active", timeout=5000)
        page.wait_for_timeout(300)

        for i, cid in enumerate(["monty", "glug", "munch", "sheets"], start=4):
            page.click(f'.chat-card[data-chat="{cid}"]')
            page.wait_for_selector("#screen-chat.active", timeout=5000)
            page.wait_for_timeout(450)
            shot(page, f"sitemap-{i:02d}-chat-{cid}.png")
            page.click("#btn-back")
            page.wait_for_selector("#screen-chatlist.active", timeout=5000)
            page.wait_for_timeout(250)

        page.click("#btn-settings")
        page.wait_for_selector("#screen-settings.active", timeout=10000)
        page.wait_for_timeout(600)
        shot(page, "sitemap-08-settings-safety-rules.png")
        page.click("#btn-settings-back")
        page.wait_for_selector("#screen-chatlist.active", timeout=5000)
        page.wait_for_timeout(250)

        if not page.locator("#mode-toggle-input").is_checked():
            page.click("#mode-toggle")
        page.wait_for_timeout(500)

        page.click("#btn-captures")
        page.wait_for_selector("#screen-captures.active", timeout=5000)
        page.wait_for_function(
            """() => {
              const root = document.querySelector('#captures-content');
              return root && !root.querySelector('.captures-loading');
            }""",
            timeout=15000,
        )
        page.wait_for_timeout(400)
        shot(page, "sitemap-09-desk-captures-gallery.png")
        page.click("#btn-captures-back")
        page.wait_for_selector("#screen-chatlist.active", timeout=5000)
        page.wait_for_timeout(250)

        page.click("#btn-camera")
        page.wait_for_selector("#screen-camera.active", timeout=10000)
        page.wait_for_timeout(1200)
        shot(page, "sitemap-10-live-desk-analysis-tab.png")
        page.click("#tab-live-clean")
        page.wait_for_timeout(600)
        shot(page, "sitemap-11-live-desk-camera-tab.png")

        page.click("#btn-cam-back")
        page.wait_for_selector("#screen-chatlist.active", timeout=5000)

        browser.close()

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
