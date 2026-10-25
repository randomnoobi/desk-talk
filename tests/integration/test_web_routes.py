"""
The main web server (desktalk/web/app.py) on the whole app built by bootstrap, with a fake detector, a fake AI
and a FakeClock. Ported from test_web_api.py (the Flask server's tests); the exact reply shapes are checked by
tests/characterization/.
"""

from __future__ import annotations

import json

from desktalk.domain import BBox, Detection
from desktalk.web.app import create_app
from tests.integration.app_kit import CUP_FAR, LAPTOP, PAIR_KEY, DeskRig, changed, desk_config
from tests.integration.web_kit import ON_THE_WIFI, links_for, web_client

KEY = Detection('key', 0.9, BBox(50, 50, 80, 70))
MB = 1024 * 1024


def fake_ai(request) -> str:
    """Answers in character and logs a sip when asked."""
    if 'water' in str(request['messages'][-1]['content']).lower():
        return 'Splash, nice one! [LOG_SIP]'
    return 'Hey! All good over here on the desk.'


# ── the Pi and the live view ─────────────────────────────────────────
def test_the_pi_gets_its_settings_including_its_detector_only_with_the_key(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    assert client.get('/api/pi/config').status_code == 403
    reply = client.get('/api/pi/config', headers={'X-Desk-Key': PAIR_KEY}).json()
    assert reply['camera_source'] == 'pi'
    assert reply['pi']['fps'] == rig.config.pi.fps
    assert reply['pi']['detector']['url'] == rig.config.pi.detector.url


def test_live_shows_the_agents_the_detections_and_the_pi_command_on_this_computer_only(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    live = web_client(rig.app).get('/api/live').json()
    assert [a['id'] for a in live['agents'] if a['on_desk']] == ['computer']
    assert live['detections']['objects']['computer']['class'] == 'laptop'
    assert live['camera_info']['frames_received'] == 12
    assert live['pi_command'] == f'python3 -m raspberry_pi.camera --server http://192.168.1.20:5000 --key {PAIR_KEY}'
    assert 'pi_command' not in web_client(rig.app, from_ip=ON_THE_WIFI).get('/api/live').json()


def test_the_latest_picture_and_proof_photos_are_served(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    assert client.get('/api/latest_frame_raw/0').status_code == 404
    rig.watch(1, LAPTOP)
    picture = client.get('/api/latest_frame_raw/0')
    assert picture.status_code == 200 and picture.content == rig.jpeg
    photo_id = rig.app.photos.save('Test')
    photo = client.get(f'/api/snapshot/{photo_id}')
    assert photo.status_code == 200 and photo.headers['cache-control'] == 'public, max-age=3600'
    assert client.get('/api/snapshots').json()['items'][0]['label'] == 'Test'
    assert client.get('/api/snapshot/nope').status_code == 404


# ── chat ─────────────────────────────────────────────────────────────
def test_a_one_on_one_reply_logs_the_habit_the_ai_asked_for(tmp_path):
    rig = DeskRig(tmp_path, ai_reply=fake_ai)
    rig.watch(4, CUP_FAR)
    reply = web_client(rig.app).post('/api/chat', json={'chat': 'cup', 'message': 'I just drank some water'}).json()
    assert reply['messages'] == [{'character': 'cup', 'text': 'Splash, nice one!'}]
    assert reply['habits_changed'] is True
    assert rig.app.habits.today(rig.clock.now())['hydration']['sips'] == 1
    system = rig.ai_clients[0].requests[-1]['messages'][0]['content']
    assert 'You are Cup' in system and 'LIVE DESK STATUS' in system


def test_a_group_message_comes_back_and_over_the_live_stream_with_the_same_id(tmp_path):
    rig = DeskRig(tmp_path, ai_reply=fake_ai)
    client = web_client(rig.app)
    nobody = client.post('/api/chat', json={'chat': 'group', 'message': 'hello?'}).json()
    assert nobody['messages'] == [] and 'Nobody is on the desk' in nobody['error']
    rig.watch(4, LAPTOP)
    rig.messages()
    reply = client.post('/api/chat', json={'chat': 'group', 'message': 'hello?'}).json()
    [said] = reply['messages']
    assert said['character'] == 'computer' and reply['streamed'] is True
    assert [m['id'] for m in rig.messages()] == [said['id']]


def test_chat_refuses_an_empty_message_and_an_unknown_agent(tmp_path):
    client = web_client(DeskRig(tmp_path, ai_reply=fake_ai).app)
    empty = client.post('/api/chat', json={'chat': 'group', 'message': '   '})
    assert (empty.status_code, empty.json()) == (400, {'error': 'empty message'})
    nobody = client.post('/api/chat', json={'chat': 'nobody', 'message': 'hi'})
    assert (nobody.status_code, nobody.json()) == (400, {'error': 'unknown chat'})


def test_typing_and_chatting_hold_off_the_small_talk(tmp_path):
    rig = DeskRig(tmp_path, ai_reply=fake_ai)
    rig.clock.advance(30)
    reply = web_client(rig.app).post('/api/activity', content=b'', headers={'Content-Type': 'text/plain'})
    assert (reply.status_code, reply.content) == (204, b'')
    assert rig.app.character_memory.user_active_at() == rig.clock.now()


def test_a_lost_item_question_says_where_it_was_seen(tmp_path):
    rig = DeskRig(tmp_path, ai_reply=fake_ai)
    rig.watch(2, KEY)
    reply = web_client(rig.app).post('/api/chat', json={'chat': 'group', 'message': 'have you seen my keys?'}).json()
    assert reply['lost_object_lookup'] is True
    assert reply['messages'][0]['cta']['action'] == 'captures'


def test_the_to_do_list_sent_by_the_app_is_kept_for_the_agent(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    todo = {'type': 'todo', 'items': [{'text': 'Send the report', 'done': False}]}
    assert client.post('/api/chat_todos', json={'chat': 'paper', 'widget': todo}).json() == {'ok': True}
    assert rig.app.desk_status.open_todos() == ['Send the report']
    bad = client.post('/api/chat_todos_sync', json={'by_chat': ['paper']})
    assert bad.status_code == 400 and 'by_chat' in bad.json()['error']


# ── habits and preferences ───────────────────────────────────────────
def test_habit_buttons_log_and_unknown_habits_are_refused(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    glass = client.post('/api/habits/log', json={'habit': 'hydration', 'action': 'glass'}).json()
    assert glass['hydration']['water_ml'] == rig.config.habit_tracker.glass_ml
    food = client.post('/api/habits/log', json={'habit': 'food', 'action': 'food', 'label': 'apple',
                                                 'healthy': True}).json()
    assert food['food']['entries'][0]['label'] == 'apple'
    nope = client.post('/api/habits/log', json={'habit': 'nope', 'action': 'x'})
    assert nope.status_code == 400 and 'nope' in nope.json()['error']
    assert 'vision' in client.get('/api/habits/today').json()


def test_preferences_change_and_the_old_names_still_work(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    reply = client.put('/api/preferences', json={'wake_time': '06:30', 'quiet_enabled': True}).json()
    assert reply['preferences']['wake_time'] == '06:30' and reply['preferences']['quiet_enabled'] is True
    bad = client.put('/api/preferences', json={'wake_time': 'noon'})
    assert bad.status_code == 400 and 'noon' in bad.json()['error']
    old = client.put('/api/preferences', json={'dnd_enabled': False, 'dnd_start_hour': 21}).json()
    assert (old['preferences']['quiet_enabled'], old['preferences']['quiet_start_hour']) == (False, 21)
    assert rig.app.store.load('preferences')['wake_time'] == '06:30'
    assert client.get('/api/preferences').json()['min_gap_sec'] == rig.config.reminders.min_gap_sec


def test_a_list_size_that_is_not_a_number_is_a_bad_request(tmp_path):
    client = web_client(DeskRig(tmp_path).app)
    for path in ('/api/habit_log?limit=lots', '/api/log/events?limit=all', '/api/log/detections?limit=some'):
        reply = client.get(path)
        assert reply.status_code == 400 and 'limit' in reply.json()['error'], path


def test_list_sizes_are_kept_within_the_settings(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    client = web_client(rig.app)
    assert len(client.get('/api/log/detections?limit=100000').json()['detections']) == 12
    assert len(client.get('/api/log/detections?limit=-5').json()['detections']) == 1


# ── the event log ────────────────────────────────────────────────────
def test_the_event_log_routes_read_and_compact_the_log(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(5, LAPTOP)
    client = web_client(rig.app)
    today = client.get('/api/log/days').json()['today']
    assert client.get('/api/log/days').json()['days'] == [today]
    assert {i['label'] for i in client.get('/api/log/presence').json()['intervals']} == {'laptop', 'person'}
    joined = client.get('/api/log/events?types=agent_joined').json()['events']
    assert [e['agent'] for e in joined] == ['computer']
    assert client.get('/api/log/detections?limit=1').json()['detections'][0]['type'] == 'detection'
    status = client.get('/api/log/status').json()
    assert status['today']['pictures_waiting'] == 15
    assert {'every_sec', 'last_error', 'name', 'next_in_sec', 'runs'} == set(status['scheduler'][0])
    assert client.post('/api/log/compact').json()['frames'] == 15
    assert client.get('/api/log/status').json()['today']['pictures_waiting'] == 0


def test_a_day_that_is_not_a_date_reads_today_instead(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(1, LAPTOP)
    client = web_client(rig.app)
    today = client.get('/api/log/days').json()['today']
    for day in ('yesterday', '../a-bc-de', '2026-10-2x'):
        assert client.get(f'/api/log/presence?day={day}').json()['day'] == today, day


# ── settings ─────────────────────────────────────────────────────────
def test_marking_the_desk_corners_saves_them_in_the_rules(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    few = client.put('/api/rules', json={'desk_outline': [[0, 0], [1, 0]]})
    assert (few.status_code, few.json()) == (400, {'error': 'Mark at least 3 corners of the desk'})
    assert client.put('/api/rules', json={}).json() == {'error': 'Empty payload'}
    outline = [[0, 0.5], [1, 0.5], [1, 1.5], [0, 1]]
    assert client.put('/api/rules', json={'desk_outline': outline}).json() == {'ok': True}
    saved = rig.app.config_store.load('safety_rules')
    assert saved['desk_outline'] == [[0, 0.5], [1, 0.5], [1, 1], [0, 1]] and 'danger_zones' in saved
    assert saved['camera_reference'] is None                   # only the phone says how it's tilted
    assert client.get('/api/rules').json()['desk_outline'] == saved['desk_outline']



def test_ui_settings_reply(tmp_path):
    """GET /api/ui_settings: the numbers and words the app page used to keep its own copies of."""
    rig = DeskRig(tmp_path)
    c = rig.config
    ui = web_client(rig.app).get('/api/ui_settings').json()
    assert ui['web']['refresh_ms'] == {'live': 1500, 'habits': 15000, 'photos': 10000, 'log': 20000}
    assert (ui['web']['request_timeout_ms'], ui['web']['toast_ms'], ui['web']['activity_ping_ms']) == (8000, 4000, 3000)
    assert (ui['web']['log_shown'], ui['web']['photos_shown'], ui['web']['habit_log_shown']) == (150, 6, 40)
    assert ui['web']['palette'][-1] == 'ink'
    assert (ui['web']['chat_kept'], ui['web']['chat_saved'], ui['web']['left_chat_shown_min']) == (200, 60, 60)
    assert ui['web']['settings_sliders'] == {'zone': {'min': 10, 'max': 200}, 'warn': {'min': 20, 'max': 200},
                                             'alert': {'min': 10, 'max': 120}}
    assert ui['web']['new_zone_px'] == 60
    assert ui['chat'] == {'message_max_chars': 1200, 'history_from_app': 10, 'todo_max_items': 20,
                          'todo_max_chars': 200}
    assert ui['habits']['sunlight_default_min'] == 10
    assert ui['habits']['morning_minutes'] == {'clear': 10, 'partly_cloudy': 20, 'overcast': 30}
    assert ui['habits']['breaks']['sit_limit_min'] == c.protocols.breaks.sit_limit_min
    assert (ui['habits']['morning_window_min'], ui['habits']['evening_dim_before_bed_min'],
            ui['habits']['stop_before_bed_hours']) == (60, 60, 2)
    assert ui['desk']['edge_proximity']['danger_px'] == 35 and ui['desk']['max_desk_corners'] == 12
    assert ui['desk']['desk_border_share'] == 0.03 and ui['desk']['min_desk_corners'] == 3
    assert ui['quiet_hours'] == {'enabled': False, 'start_hour': 22, 'end_hour': 7}
    assert ui['pi'] == {'fps': 3, 'delete_every_min': 10, 'compact_every_min': 10}
    assert ui['phone'] == {'fps': 3, 'max_side': 960, 'jpeg_quality': 70, 'tilt_max': 50,
                           'capture_width': 1280, 'capture_height': 720}
    assert ui['lens_card'] == {'id': 'lens', 'name': 'Lens', 'art': 'art/lens.svg', 'color': 'coral',
                               'about': 'The camera. Speaks up when the view needs fixing.'}
    assert ui['chat_reply_timeout_ms'] > ui['web']['request_timeout_ms']


NO_CAP_MS = 10 ** 9   # a cap so high the tests above see the worked-out wait itself


def test_ui_chat_timeout_covers_the_slowest_server_reply(tmp_path):
    """The page waits for a chat reply as long as the server may keep trying the AI (Ollama loading a model
    can take its whole 90 s, three times), so it never gives up while the server is still on a try."""
    config = changed(desk_config(tmp_path), 'llm', ollama_timeout_sec=90, max_retries=3, timeout_sec=30)
    config = changed(config, 'web', chat_reply_max_ms=NO_CAP_MS)
    ui = web_client(DeskRig(tmp_path, config=config).app).get('/api/ui_settings').json()
    assert ui['chat_reply_timeout_ms'] >= 270_000
    slower = changed(config, 'llm', ollama_timeout_sec=120)
    assert web_client(DeskRig(tmp_path / 'b', config=slower).app).get('/api/ui_settings').json()[
        'chat_reply_timeout_ms'] > ui['chat_reply_timeout_ms']


def test_ui_chat_timeout_covers_every_ai_call_one_message_can_make(tmp_path):
    """One chat message can make several AI calls in a row: a group message asks each agent it names in turn
    (at most agents.max_agents), and a lost-item question can look twice for each item and then ask the chat AI.
    The page waits for the most of those."""
    config = changed(desk_config(tmp_path), 'agents', max_agents=3)
    config = changed(config, 'lost_items', items={})
    config = changed(config, 'web', chat_reply_max_ms=NO_CAP_MS)
    one_call_ms = round(config.llm.longest_reply_sec() * 1000)
    request_ms = config.web.request_timeout_ms

    def wait(c, folder):
        return web_client(DeskRig(tmp_path / folder, config=c).app).get('/api/ui_settings').json()[
            'chat_reply_timeout_ms']

    assert wait(config, 'a') == 3 * one_call_ms + request_ms                     # three agents, one after another
    four_items = desk_config(tmp_path / 'full').lost_items.items
    assert len(four_items) == 4
    more_items = changed(config, 'lost_items', items=four_items)
    assert wait(more_items, 'b') == (2 * 4 + 1) * one_call_ms + request_ms       # two looks per item, then the answer


def test_ui_chat_timeout_stops_at_the_cap(tmp_path):
    """The worst case (every agent, every try) can add up to over an hour, which looks like a frozen page. The
    page stops waiting at web.chat_reply_max_ms and shows the error, unless the worked-out wait is shorter."""
    config = changed(desk_config(tmp_path), 'web', chat_reply_max_ms=180_000)

    def wait(c, folder):
        return web_client(DeskRig(tmp_path / folder, config=c).app).get('/api/ui_settings').json()[
            'chat_reply_timeout_ms']

    assert wait(config, 'a') == 180_000
    quick = changed(config, 'llm', ollama_timeout_sec=1, timeout_sec=1, max_retries=1, min_interval_sec=0)
    quick = changed(quick, 'agents', max_agents=1)
    assert wait(quick, 'b') < 180_000


# ── what every reply has in common ───────────────────────────────────
# Routes nothing called, taken out in the 2026-10 cleanup. They answer like any path the server doesn't know:
# 404 to a GET, and 405 to anything else (only the page's files are left at that path, and they only GET).
REMOVED_ROUTES = (
    ('GET', '/api/context'), ('GET', '/api/daily_stats'), ('GET', '/api/habit_policy'), ('PUT', '/api/habit_policy'),
    ('GET', '/api/detections'), ('GET', '/api/safety'), ('POST', '/api/safety'), ('GET', '/api/scenarios'),
    ('GET', '/api/phone/info'), ('GET', '/api/video_feed'), ('GET', '/api/latest_frame/0'),
)


def test_removed_routes_answer_404(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(1, LAPTOP)                        # a picture, so a video route that is still there would answer
    client = web_client(rig.app)
    for method, path in REMOVED_ROUTES:
        reply = client.request(method, path, json={} if method != 'GET' else None)
        assert reply.status_code == (404 if method == 'GET' else 405), f'{method} {path}'


def test_errors_say_error_not_detail_and_every_reply_allows_any_page(tmp_path):
    client = web_client(DeskRig(tmp_path).app)
    missing = client.get('/api/nothing-here')
    assert missing.status_code == 404 and set(missing.json()) == {'error'}
    assert missing.headers['access-control-allow-origin'] == '*'
    assert client.get('/api/status').headers['access-control-allow-origin'] == '*'


def test_a_route_that_crashes_still_answers_with_an_error(tmp_path, monkeypatch):
    rig = DeskRig(tmp_path)

    def broken_disk() -> list[str]:
        raise OSError('the disk is gone')

    monkeypatch.setattr(rig.app.event_log, 'days', broken_disk)
    reply = web_client(rig.app).get('/api/log/days')
    assert reply.status_code == 500 and set(reply.json()) == {'error'}
    assert 'the disk is gone' not in reply.json()['error']          # the details go to the log, not the page


def test_a_trailing_slash_is_not_redirected(tmp_path):
    assert web_client(DeskRig(tmp_path).app).get('/api/live/').status_code == 404


def test_a_request_above_the_size_limit_is_refused(tmp_path):
    config = changed(desk_config(tmp_path), 'web', max_request_mb=0.25)
    client = web_client(DeskRig(tmp_path, config=config).app)
    reply = client.post('/api/chat', content=json.dumps({'message': 'x' * (MB // 2)}),
                        headers={'Content-Type': 'application/json'})
    assert reply.status_code == 413 and '0.25 MB' in reply.json()['error']


def test_the_browser_app_is_served_and_always_checked_again(tmp_path):
    client = web_client(DeskRig(tmp_path).app)
    page = client.get('/')
    assert page.status_code == 200 and page.text.startswith('<!DOCTYPE html>')
    script = client.get('/app.js')
    assert script.headers['cache-control'] == 'no-cache'
    assert client.get('/art/desk.svg').headers['content-type'] == 'image/svg+xml; charset=utf-8'
    assert client.get('/no-such-file.js').status_code == 404


def test_the_phone_link_shows_only_on_this_computer(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    web = create_app(rig.app, links_for(rig.app, phone_page=True))
    here = web_client(web).get('/api/live?qr=1').json()['phone']
    assert here['url'] == f'https://192.168.1.20:{rig.config.camera.phone_port}/phone?key={PAIR_KEY}'
    assert here['qr_svg'].startswith('<svg')
    there = web_client(web, from_ip=ON_THE_WIFI).get('/api/live?qr=1').json()['phone']
    assert (there['url'], there['qr_svg']) == ('', None)


def test_the_agents_lens_the_status_and_the_recent_messages_have_their_values(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    client = web_client(rig.app)
    assert client.get('/api/agents').json()['lens']['name'] == 'Lens'
    assert client.get('/api/status').json()['agents'] == ['computer']
    assert client.get('/api/messages/recent').json()['messages'][0]['text'] == 'Computer joined the chat'
    assert client.get('/api/habit_log').json()['summary']['breaks_today'] == 0
