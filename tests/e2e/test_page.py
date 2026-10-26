"""
The app's real page (web_app/) in a headless Chromium, on the whole app on a real server
(tests/e2e_kit/server.py). Through the page and HTTP only, so the clean-up can't quietly break what you see.

    uv run playwright install chromium      # once
    uv run pytest -m e2e tests/e2e
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from desktalk.domain import BBox, Detection
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
        card = page.locator('.habit-card').filter(has=page.get_by_role('heading', name='Water'))
        expect(card.get_by_role('button', name='+ Glass')).to_have_count(0)      # only "+ Sip" now
        card.get_by_role('button', name='+ Sip').click()
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
        return changed(config, 'pi', delete_every_min=7)

    with app_server(tmp_path, pictures=pictures, change=change) as server:
        open_app(page, server.url)                  # no pictures yet: the desk cam says how to start the Pi
        expect(page.locator('#cam-stage')).to_contain_text('deletes them every 7 minutes')


def watch_for_errors(page: Any) -> list[str]:
    errors: list[str] = []
    page.on('pageerror', lambda exc: errors.append(f'page error: {exc}'))
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
    return errors


PAGES = {'habits': 'Habits', 'log': 'Event log', 'captures': 'Captures', 'settings': 'Settings'}
DEMO_CHATS = ('Computer', 'Cup', 'Snack', 'Book')      # each has its own panel (breaks, water, food, to-do)


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
        tracker = page.locator('#tracker')
        expect(tracker).to_contain_text('put it back')               # the lifted-and-put-back sip rule
        expect(tracker.get_by_role('button', name='+ Glass')).to_have_count(0)
        visit_every_page(page)
        expect(page.locator('#view-settings')).to_contain_text('Desk edges')
        page.locator('.page-link[data-view="habits"]').click()
        habits = page.locator('#view-habits')
        expect(habits).to_contain_text('desk warnings')
        for gone in ('Nail biting', 'nail biting', 'Camera log', 'Protocols', 'lux', '/255'):
            expect(habits).not_to_contain_text(gone)
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


# The demo's agents have no phone-free panel, but an agent with one can join (as on the real desk).
DEMO_EXTRA_PANELS = {'Phone': 'phone_free'}


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
        expect(page.locator('#tracker')).not_to_contain_text(re.compile(r'undefined|NaN|Protocols|slouching'))
    assert [e for e in errors if not FONT_FROM_FILE.search(e)] == []


def test_the_light_panel_is_gone(page):
    """Lamp can't join the chat any more, so its light panel went (light stays on the Habits page)."""
    page.goto((APP_FOLDER / 'index.html').as_uri(), wait_until='domcontentloaded')
    expect(chat_link(page, DEMO_CHAT)).to_be_visible()
    page.evaluate('''() => {
        const joinedAt = Date.now() / 1000;
        upsertAgent({ id: 'lamp', name: 'Lamp', color: 'moss', tracker: 'light', on_desk: true, joined_at: joinedAt });
        renderChatList();
    }''')
    chat_link(page, 'Lamp').click()
    expect(page.locator('#chat-name')).to_have_text('Lamp')
    expect(page.locator('#tracker')).to_be_hidden()


def test_typing_in_a_panel_survives_a_refresh(page):
    """The panels are drawn again every few seconds; that must not wipe a to-do you're half-way through typing."""
    page.goto((APP_FOLDER / 'index.html').as_uri(), wait_until='domcontentloaded')
    expect(chat_link(page, 'Book')).to_be_visible()
    chat_link(page, 'Book').click()
    task = page.locator('#tracker').get_by_label('New task')
    task.fill('half a thou')
    page.evaluate('async () => { state.habits = demoHabits(); await refreshHabits(); }')
    expect(task).to_have_value('half a thou')
    expect(task).to_be_focused()


FIRST_CHECK_WITHIN_MS = 10_000      # longer than the page's own wait for its first check (FIRST_CHECK_MS in app.js)


def test_a_server_that_never_answers_the_first_check_gets_the_demo(page):
    """The page asks the server for its settings when it opens. If nothing answers, it shows that it is still
    connecting, clicks wait for the settings (no errors), and after its own time limit it runs the demo."""
    errors = watch_for_errors(page)
    with static_site(APP_FOLDER) as url:
        held: list[Any] = []

        def hold(route: Any) -> None:                         # not answered (until the end of the test)
            held.append(route)

        page.route('**/api/**', hold)
        page.goto(url, wait_until='domcontentloaded')
        expect(page.locator('#conn')).to_have_attribute('data-state', 'checking')
        expect(page.locator('#conn-text')).to_have_text('Connecting…')
        page.locator('.page-link[data-view="habits"]').click()
        page.locator('#conn').click()
        assert errors == []
        expect(page.locator('#conn')).to_have_attribute('data-state', 'demo', timeout=FIRST_CHECK_WITHIN_MS)
        expect_the_demo(page)
        for route in held:                                    # let go of them, so the browser can close cleanly
            route.abort()
    assert held and errors == []


RETRY_WITHIN_MS = 15_000         # the demo asks again after web.retry_start_sec (3 s in the demo), then 6 s ...


def test_a_server_that_answers_late_switches_the_demo_to_live(page, tmp_path, pictures):
    """A server that's slow to start: the page runs the demo, keeps asking, and goes live once it answers."""
    with running_app(tmp_path, pictures=pictures) as url:
        down = {'now': True}

        def maybe(route: Any) -> None:
            if down['now']:
                route.abort()
            else:
                route.continue_()

        page.route('**/api/ui_settings', maybe)
        page.goto(url, wait_until='domcontentloaded')
        expect(page.locator('#conn')).to_have_attribute('data-state', 'demo')
        down['now'] = False
        expect(page.locator('#conn')).to_have_attribute('data-state', LIVE, timeout=RETRY_WITHIN_MS)
        expect(page.locator('#conn-text')).to_have_text(CAMERA_ON)


def test_heads_up_shows_camera_problems(page, tmp_path, pictures):
    """A camera problem in "Heads up" (with Lens's picture) used to stop the page."""
    errors = watch_for_errors(page)
    problem = {'title': 'The camera is covered', 'fix': 'Take your hand off it.'}

    def with_a_problem(route: Any) -> None:
        reply = route.fetch()
        body = reply.json()
        body['camera_issues'] = [problem]
        route.fulfill(response=reply, json=body)

    with running_app(tmp_path, pictures=pictures) as url:
        page.route('**/api/live*', with_a_problem)
        open_app(page, url)
        expect(page.locator('#risks')).to_contain_text(problem['title'])
        expect(page.locator('#risks')).to_contain_text(problem['fix'])
    assert errors == []


def test_sensitivity_slider_saves(page, tmp_path, pictures):
    """The Settings page's sensitivity slider shows the step's name and its two numbers, and saves the step."""
    with running_app(tmp_path, pictures=pictures) as url:
        steps = page.request.get(f'{url}/api/ui_settings').json()['sensitivity_steps']
        picky = steps[0]
        open_app(page, url)
        page.locator('.page-link[data-view="settings"]').click()
        slider = page.get_by_label('Sensitivity')
        expect(slider).to_have_attribute('max', str(len(steps) - 1))
        slider.fill('0')
        says = page.locator('#sensitivity-says')
        expect(says).to_contain_text(picky['name'])
        expect(says).to_contain_text(f"{round(picky['min_confidence'] * 100)}%")
        expect(says).to_contain_text(f"{picky['join_after_sec']:g} seconds")
        page.get_by_role('button', name='Save settings').click()
        expect(page.locator('#saved')).to_have_text('Saved')
        assert page.request.get(f'{url}/api/preferences').json()['sensitivity'] == 0
        page.reload()
        expect(page.locator('#conn')).to_have_attribute('data-state', LIVE)
        page.locator('.page-link[data-view="settings"]').click()
        expect(page.get_by_label('Sensitivity')).to_have_value('0')


SERVER_WITHIN_SEC = 5            # what the page sent reaches the server
POLL_SEC = 0.1


def wait_for(check: Any, within: float = SERVER_WITHIN_SEC) -> bool:
    """Asks check() every POLL_SEC until it says yes (True) or `within` seconds pass (False)."""
    give_up = time.monotonic() + within
    while not check():
        if time.monotonic() > give_up:
            return False
        time.sleep(POLL_SEC)
    return True


BOOK = Detection('book', 0.9, BBox(100, 300, 220, 380))
BOOK_CHAT = 'Book'                           # config/agents.yaml: a book is Book (its id is still paper)


def test_todo_due_date_shown(page, tmp_path, pictures):
    """Book's panel: a date box next to "Add a task", each to-do shows its due date, late ones stand out, and
    the server keeps the list (a fresh browser gets it back)."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with app_server(tmp_path, pictures=pictures, found={2: [BOOK]}) as server:
        server.show(2)
        open_app(page, server.url)
        expect(chat_link(page, BOOK_CHAT)).to_be_visible(timeout=JOIN_WITHIN_MS)
        chat_link(page, BOOK_CHAT).click()
        tracker = page.locator('#tracker')
        tracker.get_by_label('New task').fill('Essay')
        tracker.get_by_label('Due date').fill(yesterday)
        tracker.get_by_role('button', name='Add').click()
        essay = tracker.locator('.todo li').filter(has_text='Essay')
        expect(essay).to_contain_text('was due yesterday')
        expect(essay).to_have_class(re.compile(r'\blate\b'))
        expect(tracker.get_by_label('New task')).to_have_value('')
        saved = {'text': 'Essay', 'done': False, 'due': yesterday}
        assert wait_for(lambda: saved in page.request.get(f'{server.url}/api/todos').json()['items'])

        page.evaluate('localStorage.clear()')      # a fresh browser: the server's list wins
        page.reload()
        expect(chat_link(page, BOOK_CHAT)).to_be_visible(timeout=JOIN_WITHIN_MS)
        chat_link(page, BOOK_CHAT).click()
        expect(page.locator('#tracker .todo li').filter(has_text='Essay')).to_contain_text('was due yesterday')


def test_event_log_shows_only_habits(page, tmp_path, pictures):
    """The Event log shows sips, reminders and warnings, not joins, camera numbers or the raw printout."""
    with app_server(tmp_path, pictures=pictures) as server:
        server.show(2)
        open_app(page, server.url)
        expect(chat_link(page, BOTTLE_CHAT)).to_be_visible(timeout=JOIN_WITHIN_MS)     # a join is logged
        assert page.request.post(f'{server.url}/api/habits/log', data={'habit': 'hydration', 'action': 'sip'}).ok
        page.locator('.page-link[data-view="log"]').click()
        log = page.locator('#view-log')
        filters = log.get_by_role('group', name='Show')
        expect(filters.get_by_role('button')).to_have_text(['Everything', 'Habits', 'Reminders', 'Warnings'])
        expect(log.locator('.log.events')).to_contain_text('Water')
        for gone in ('joined the chat', 'Compact now', 'pictures a second', 'detections.jsonl', 'On the desk'):
            expect(log).not_to_contain_text(gone)
        filters.get_by_role('button', name='Warnings').click()
        expect(filters.get_by_role('button', name='Warnings')).to_have_attribute('aria-pressed', 'true')
        expect(log.locator('.log.events')).to_have_count(0)
        filters.get_by_role('button', name='Habits').click()
        expect(log.locator('.log.events')).to_contain_text('Water')
        days = log.get_by_role('group', name='Day')
        expect(days.get_by_role('button', name='Today')).to_have_attribute('aria-pressed', 'true')
