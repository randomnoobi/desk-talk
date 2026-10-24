"""
The app's real page (web_app/) in a headless Chromium, on the whole app on a real server
(tests/e2e_kit/server.py). Through the page and HTTP only, so the clean-up can't quietly break what you see.

    uv run playwright install chromium      # once
    uv run pytest -m e2e tests/e2e
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.e2e_kit.server import BOTTLE_CHAT, CANNED_REPLY, app_server, desk_pictures, running_app

pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect  # noqa: E402  (only when Playwright is installed)

pytestmark = pytest.mark.e2e

JOIN_WITHIN_MS = 5_000           # the plan: the bottle's chat shows up within 5 s of picture 2
REPLY_WITHIN_MS = 15_000
OFFLINE_WITHIN_MS = 10_000
LIVE = re.compile(r'^(live|waiting)$')          # #conn's state once the page talks to the server (not the demo)
CAMERA_ON = 'Pi camera on'
OFFLINE_NOTE = re.compile(r"can.t reach Desk Talk", re.IGNORECASE)


def open_app(page: Any, url: str) -> None:
    page.goto(url, wait_until='domcontentloaded')
    expect(page.locator('#conn')).to_have_attribute('data-state', LIVE)


def chat_link(page: Any, name: str) -> Any:
    return page.locator('#chat-list .chat-link').filter(has=page.locator('.chat-link-name', has_text=name))


def water_today(page: Any) -> Any:
    return page.locator('.habit-card').filter(has=page.get_by_role('heading', name='Water')).locator('.big')


@pytest.fixture
def pictures(tmp_path: Path) -> list[Path]:
    return desk_pictures(tmp_path)


def test_page_loads_and_shows_lens(page, tmp_path, pictures):
    with running_app(tmp_path, pictures=pictures) as url:
        open_app(page, url)
        expect(page.locator('#conn-text')).to_have_text(CAMERA_ON)
        expect(chat_link(page, 'The desk')).to_be_visible()
        expect(page.locator('#chat-name')).to_have_text('The desk')
        # Today the page keeps Lens out of the chat list (Lens talks in the group chat, and in the desk cam's
        # "Heads up" list); the Lens card the page can show comes from /api/agents.
        lens = page.request.get(f'{url}/api/agents').json()['lens']
        assert lens['id'] == 'lens' and lens['name'] == 'Lens' and lens['art'] == 'art/lens.svg'
        expect(page.locator(f'img[src="{lens["art"]}"]')).to_have_count(0)     # not in the chat list
        assert page.request.get(f'{url}/{lens["art"]}').ok                     # its drawing is served


def test_bottle_on_desk_joins_the_chat(page, tmp_path, pictures):
    with app_server(tmp_path, pictures=pictures) as server:
        server.show(1)
        open_app(page, server.url)
        expect(page.locator('#conn-text')).to_have_text(CAMERA_ON)
        expect(chat_link(page, BOTTLE_CHAT)).to_have_count(0)
        server.show(2)
        expect(chat_link(page, BOTTLE_CHAT)).to_be_visible(timeout=JOIN_WITHIN_MS)
        expect(page.locator('#messages')).to_contain_text(f'{BOTTLE_CHAT} joined the chat')


def test_sending_a_message_gets_a_reply(page, tmp_path, pictures):
    # Lens has no chat of its own on today's page, so this talks to the bottle (Cup) in its own chat.
    with app_server(tmp_path, pictures=pictures) as server:
        server.show(2)
        open_app(page, server.url)
        chat_link(page, BOTTLE_CHAT).click()
        expect(page.locator('#chat-name')).to_have_text(BOTTLE_CHAT)
        page.locator('#input').fill('hi')
        page.locator('#input').press('Enter')
        expect(page.locator('#messages .msg.mine .bubble')).to_have_text('hi')
        expect(page.locator('#messages .msg:not(.mine) .bubble').last).to_have_text(CANNED_REPLY,
                                                                                    timeout=REPLY_WITHIN_MS)


def test_habit_log_click_updates_today(page, tmp_path, pictures):
    with running_app(tmp_path, pictures=pictures) as url:
        open_app(page, url)
        page.locator('.page-link[data-view="habits"]').click()
        water = water_today(page)
        expect(water).to_contain_text('0.0 L')
        before = page.request.get(f'{url}/api/habits/today').json()['hydration']['water_ml']
        page.locator('.habit-card').filter(has=page.get_by_role('heading', name='Water')).get_by_role(
            'button', name='+ Glass').click()
        expect(water).not_to_contain_text('0.0 L')
        after = page.request.get(f'{url}/api/habits/today').json()['hydration']['water_ml']
        assert after > before
        expect(water).to_contain_text(f'{after / 1000:.1f} L')


@pytest.mark.xfail(strict=True, reason="Today's page shows nothing when the server goes away: no offline note "
                                       '(Review Focus 3; the plan has Task 9 rely on one)')
def test_page_recovers_after_server_restart(page, tmp_path, pictures):
    with app_server(tmp_path, pictures=pictures) as server:
        server.show(1)
        open_app(page, server.url)
        expect(page.locator('#conn-text')).to_have_text(CAMERA_ON)
        server.stop()
        expect(page.get_by_text(OFFLINE_NOTE)).to_be_visible(timeout=OFFLINE_WITHIN_MS)
        server.start()
        expect(page.get_by_text(OFFLINE_NOTE)).to_have_count(0, timeout=OFFLINE_WITHIN_MS)
        expect(page.locator('#conn-text')).to_have_text(CAMERA_ON)


def test_page_keeps_working_after_server_restart(page, tmp_path, pictures):
    """The part of Review Focus 3 today's page does: without a reload, it picks up the restarted server."""
    with app_server(tmp_path, pictures=pictures) as server:
        server.show(1)
        open_app(page, server.url)
        expect(page.locator('#conn-text')).to_have_text(CAMERA_ON)
        server.stop()
        server.start()
        server.show(2)
        expect(chat_link(page, BOTTLE_CHAT)).to_be_visible(timeout=OFFLINE_WITHIN_MS)
        chat_link(page, BOTTLE_CHAT).click()
        page.locator('#input').fill('hi')
        page.locator('#input').press('Enter')
        expect(page.locator('#messages .msg:not(.mine) .bubble').last).to_have_text(CANNED_REPLY,
                                                                                    timeout=REPLY_WITHIN_MS)
