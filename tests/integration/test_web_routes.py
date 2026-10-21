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


def test_the_safety_pipeline_can_set_the_safety_state(tmp_path):
    rig = DeskRig(tmp_path)
    client = web_client(rig.app)
    pairs = [{'objects': ['cup', 'laptop'], 'distance_px': 42.5}]
    assert client.post('/api/safety', json={'type': 'danger_alert', 'pairs': pairs}).json() == {'ok': True}
    assert client.get('/api/safety').json()['state'] == 'DANGEROUS'
    client.post('/api/safety', json={'type': 'safe_status'})
    assert client.get('/api/safety').json()['dangers'] == []


# ── chat ─────────────────────────────────────────────────────────────
def test_a_one_on_one_reply_logs_the_habit_the_ai_asked_for(tmp_path):
    rig = DeskRig(tmp_path, ai_reply=fake_ai)
    rig.watch(4, CUP_FAR)
    reply = web_client(rig.app).post('/api/chat', json={'chat': 'cup', 'message': 'I just drank some water'}).json()
    assert reply['messages'] == [{'character': 'cup', 'text': 'Splash, nice one!'}]
    assert reply['habits_changed'] is True
    assert rig.app.habits.today()['hydration']['sips'] == 1
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
    assert rig.app.desk.last_user_activity == rig.clock.now()


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
    old = client.put('/api/habit_policy', json={'dnd_enabled': False, 'dnd_start_hour': 21}).json()
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


def test_scenarios_list_the_lost_items_seen(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(2, KEY)
    rig.clock.advance(30)
    scenarios = web_client(rig.app).get('/api/scenarios').json()
    assert scenarios['lost_objects']['keys']['last_seen_ago_sec'] == 30
    assert scenarios['lost_items']['keys'] == {'name': rig.config.lost_items.items['keys'].name}


# ── what every reply has in common ───────────────────────────────────
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


def test_the_phone_info_shows_the_link_only_on_this_computer(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    web = create_app(rig.app, links_for(rig.app, phone_page=True))
    here = web_client(web).get('/api/phone/info').json()
    assert here['url'] == f'https://192.168.1.20:{rig.config.camera.phone_port}/phone?key={PAIR_KEY}'
    assert here['qr_svg'].startswith('<svg')
    there = web_client(web, from_ip=ON_THE_WIFI).get('/api/phone/info').json()
    assert (there['url'], there['qr_svg']) == ('', None)
