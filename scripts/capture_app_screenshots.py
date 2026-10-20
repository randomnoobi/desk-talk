#!/usr/bin/env python3
"""
Take screenshots of every screen of the Desk Talk app (for docs, a portfolio or a sitemap).

Requires: pip install playwright && playwright install chromium

Default: serves ``web_app/`` with ``python -m http.server`` on port 8765, so the app
runs its built-in demo (no server, camera or AI needed).

With Desk Talk running (``python server.py``), capture the live app instead::

  python scripts/capture_app_screenshots.py --url http://127.0.0.1:5000

Outputs: ``_screenshots/app/*.png`` (one per chat and page, plus a phone-sized view)
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
OUT_DIR = ROOT / "_screenshots" / "app"
STATIC_PORT = int(os.environ.get("SCREENSHOT_PORT", "8765"))
AUTO_BASE = f"http://127.0.0.1:{STATIC_PORT}"
PAGES = ["habits", "log", "captures", "settings"]


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


def shot(page, filename: str, full_page: bool = False) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / filename
    page.screenshot(path=str(path), full_page=full_page)
    print(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="",
        help="App URL (e.g. http://127.0.0.1:5000). If omitted, serves web_app/ and uses the demo.",
    )
    parser.add_argument("--wait", type=float, default=8.0,
                        help="Seconds to let the app settle first (the demo adds characters one by one).")
    args = parser.parse_args()

    proc = None
    base = args.url.strip().rstrip("/")

    if not base:
        proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(STATIC_PORT), "--bind", "127.0.0.1"],
            cwd=str(ROOT / "web_app"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_server(STATIC_PORT)
        base = AUTO_BASE

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.stderr.write("Install Playwright: pip install playwright && playwright install chromium\n")
        if proc:
            proc.terminate()
        sys.exit(1)

    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/", wait_until="domcontentloaded")
            page.wait_for_selector(".chat-link", timeout=15000)
            page.wait_for_timeout(int(args.wait * 1000))

            chats = page.eval_on_selector_all(".chat-link", "els => els.map(e => e.dataset.chat)")
            for i, chat in enumerate(chats, start=1):
                page.click(f'.chat-link[data-chat="{chat}"]')
                page.wait_for_timeout(700)
                shot(page, f"{i:02d}-chat-{chat}.png")

            for j, view in enumerate(PAGES, start=len(chats) + 1):
                page.click(f'.page-link[data-view="{view}"]')
                page.wait_for_timeout(1500)
                shot(page, f"{j:02d}-page-{view}.png", full_page=True)

            page.set_viewport_size({"width": 390, "height": 844})
            page.click('.chat-link[data-chat="group"]')
            page.wait_for_timeout(700)
            shot(page, f"{len(chats) + len(PAGES) + 1:02d}-phone-size-group-chat.png")
            browser.close()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    if errors:
        print("JavaScript errors on the page:", *errors, sep="\n  ")


if __name__ == "__main__":
    main()
