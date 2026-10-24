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

from tests.e2e_kit.server import BOTTLE_CHAT, CANNED_REPLY, app_server, desk_pictures, running_app, static_site
from tests.integration.app_kit import ROOT, changed

pytest.importorskip('playwright.sync_api')
from playwright.sync_api import expect  # noqa: E402  (only when Playwright is installed)

pytestmark = pytest.mark.e2e

JOIN_WITHIN_MS = 5_000           # the plan: the bottle's chat shows up within 5 s of picture 2
REPLY_WITHIN_MS = 15_000
OFFLINE_WITHIN_MS = 10_000
LIVE = re.compile(r'^(live|waiting)$')          # #conn's state once the page talks to the server (not the demo)
CAMERA_ON = 'Pi camera on'
OFFLINE_NOTE = re.compile(r"can.t reach Desk Talk", re.IGNORECASE)
APP_FOLDER = ROOT / 'web_app'
DEMO_CHAT = 'Cup'                                # one of the demo's made-up desk objects
FIRST_CHECK_404 = re.compile(r'Failed to load resource.*404')   # the demo's one "is there a server?" check
# From file:// Chromium won't load the font (index.html preloads it), so the page shows a fallback font.
FONT_FROM_FILE = re.compile(r"Access to font at 'file:|Failed to load resource: net::ERR_FAILED")


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


def test_page_shows_the_servers_numbers(page, tmp_path, pictures):
    """The page takes its numbers from GET /api/ui_settings, not from copies of its own."""
    def change(config):
        config = changed(config, 'pi', delete_every_min=7)
        return changed(config, 'event_log', compact_every_min=9)

    with app_server(tmp_path, pictures=pictures, change=change) as server:
        server.show(1)
        open_app(page, server.url)
        page.locator('.page-link[data-view="log"]').click()
        intro = page.locator('#view-log .page-intro')
        expect(intro).to_contain_text('every 9 minutes')
        expect(intro).to_contain_text('deleted every 7 minutes')


def watch_for_errors(page: Any) -> list[str]:
    errors: list[str] = []
    page.on('pageerror', lambda exc: errors.append(f'page error: {exc}'))
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
    return errors


PAGES = {'habits': 'Habits', 'log': 'Event log', 'captures': 'Captures', 'settings': 'Settings'}
DEMO_CHATS = ('Computer', 'Cup', 'Snack', 'Paper')     # each has its own panel (breaks, water, food, to-do)


def visit_every_page(page: Any) -> None:
    for view, title in PAGES.items():
        page.locator(f'.page-link[data-view="{view}"]').click()
        expect(page.locator(f'#view-{view} .page-title')).to_have_text(title)


def expect_the_demo(page: Any) -> None:
    expect(page.locator('#conn')).to_have_attribute('data-state', 'demo')
    expect(page.locator('#conn-text')).to_have_text('Demo mode')
    expect(chat_link(page, DEMO_CHAT)).to_be_visible()
    expect(page.locator('#messages')).to_contain_text(f'{DEMO_CHAT} joined the chat')
    for name in DEMO_CHATS:
        chat_link(page, name).click()
        expect(page.locator('#tracker')).to_be_visible()
    page.locator('#input').fill('hi')
    page.locator('#input').press('Enter')
    expect(page.locator('#messages .msg:not(.mine) .bubble').last).to_contain_text('only a demo')
    visit_every_page(page)
    page.locator('.page-link[data-view="habits"]').click()
    expect(water_today(page)).to_be_visible()


def test_every_page_opens_on_the_real_app(page, tmp_path, pictures):
    errors = watch_for_errors(page)
    with app_server(tmp_path, pictures=pictures) as server:
        server.show(2)
        open_app(page, server.url)
        chat_link(page, BOTTLE_CHAT).click()
        expect(page.locator('#tracker')).to_contain_text('Protocols')
        visit_every_page(page)
        expect(page.locator('#view-settings')).to_contain_text('Desk edges')
    assert errors == []


def test_page_runs_as_a_demo_from_a_file(page):
    """Opened straight from the folder (file://), the page runs as a demo without asking for a server."""
    errors = watch_for_errors(page)
    page.goto((APP_FOLDER / 'index.html').as_uri(), wait_until='domcontentloaded')
    expect_the_demo(page)
    assert [e for e in errors if not FONT_FROM_FILE.search(e)] == []


def test_page_runs_as_a_demo_without_a_server(page):
    """Like on GitHub Pages: the page's files on a plain web server, with no Desk Talk behind them."""
    errors = watch_for_errors(page)
    with static_site(APP_FOLDER) as url:
        page.goto(url, wait_until='domcontentloaded')
        expect_the_demo(page)
    assert [e for e in errors if not FIRST_CHECK_404.search(e)] == []


# The demo's agents have no light or phone-free panel, but an agent with one can join (as on the real desk).
DEMO_EXTRA_PANELS = {'Lamp': 'light', 'Phone': 'phone_free'}


def test_the_demos_panels_have_every_number_they_show(page):
    """The demo's own settings (DEMO_UI in app.js) have every number its panels show, e.g. the minutes the
    "I got sunlight" button logs."""
    errors = watch_for_errors(page)
    page.goto((APP_FOLDER / 'index.html').as_uri(), wait_until='domcontentloaded')
    expect(chat_link(page, DEMO_CHAT)).to_be_visible()
    page.evaluate('''(panels) => {
        for (const [name, tracker] of Object.entries(panels)) {
            const joinedAt = Date.now() / 1000;
            upsertAgent({ id: name.toLowerCase(), name, color: 'moss', tracker, on_desk: true, joined_at: joinedAt });
        }
        renderChatList();
    }''', DEMO_EXTRA_PANELS)
    for name in (*DEMO_CHATS, *DEMO_EXTRA_PANELS):
        chat_link(page, name).click()
        expect(page.locator('#tracker')).to_be_visible()
        expect(page.locator('#tracker')).not_to_contain_text(re.compile(r'undefined|NaN'))
        if name == 'Lamp':
            assert (page.locator('#tracker [data-sun]').get_attribute('data-sun') or '').isdigit()
    assert [e for e in errors if not FONT_FROM_FILE.search(e)] == []


FIRST_CHECK_WITHIN_MS = 10_000      # longer than the page's own wait for its first check (FIRST_CHECK_MS in app.js)


def test_a_server_that_never_answers_the_first_check_gets_the_demo(page):
    """The page asks the server for its settings when it opens. If nothing answers, it shows that it is still
    connecting, clicks wait for the settings (no errors), and after its own time limit it runs the demo."""
    errors = watch_for_errors(page)
    with static_site(APP_FOLDER) as url:
        page.route('**/api/**', lambda route: None)          # never answered
        page.goto(url, wait_until='domcontentloaded')
        expect(page.locator('#conn')).to_have_attribute('data-state', 'checking')
        expect(page.locator('#conn-text')).to_have_text('Connecting…')
        page.locator('.page-link[data-view="habits"]').click()
        page.locator('#conn').click()
        assert errors == []
        expect(page.locator('#conn')).to_have_attribute('data-state', 'demo', timeout=FIRST_CHECK_WITHIN_MS)
        expect_the_demo(page)
    assert errors == []
