"""The web API, through Flask's test client, with a fake AI."""

import json
import time

import cv2
import numpy as np
import pytest

from conftest import Feeder, blank_frame, obj
from desktalk.web.routes import create_web_app


def jpeg(value=140):
    ok, buf = cv2.imencode('.jpg', blank_frame(value=value))
    return buf.tobytes()


def reply_for(kw):
    """The fake AI: answers in character and logs a sip when asked."""
    last = kw['messages'][-1]['content']
    if 'water' in last.lower():
        return 'Splash, nice one! [LOG_SIP]'
    return 'Hey! All good over here on the desk.'


@pytest.fixture
def setup(make_app):
    app = make_app(ai_reply=reply_for, secrets={'gemini_api_key': 'fake'})
    web = create_web_app(app)
    return app, web.test_client()


def test_pi_frames_need_the_key(setup):
    app, c = setup
    assert c.post('/api/pi/frame', data=jpeg(), headers={'X-Desk-Key': 'wrong'}).status_code == 403
    assert c.post('/api/pi/frame', data=b'not a jpeg', headers={'X-Desk-Key': 'testkey'}).status_code == 400
    r = c.post('/api/pi/frame', data=jpeg(), headers={'X-Desk-Key': 'testkey', 'X-Frame-Id': 'pi-000007',
                                                     'X-Lux': '312.5', 'X-Pi-Photos': '42', 'X-Taken-At': str(time.time())})
    assert r.status_code == 200 and r.json['ok']
    ok, frame = app.source.read(timeout=0.1)
    assert ok and frame.shape == (480, 640, 3)
    assert app.source.meta['frame_id'] == 'pi-000007' and app.source.meta['lux'] == 312.5
    assert app.source.pi_info['photos_stored'] == 42
    assert app.camera_connected()


def test_pi_config_comes_from_the_settings(setup):
    _, c = setup
    assert c.get('/api/pi/config').status_code == 403
    r = c.get('/api/pi/config', headers={'X-Desk-Key': 'testkey'}).json
    assert r['pi']['fps'] == 3 and r['pi']['delete_every_min'] == 10


def test_live_status_agents_and_detections(setup):
    app, c = setup
    Feeder(app).run(4, [obj('laptop', 200, 200, 400, 350), obj('stapler', 10, 10, 50, 40)])
    live = c.get('/api/live').json
    assert {a['id'] for a in live['agents'] if a['on_desk']} == {'computer', 'stapler'}
    assert live['detections']['objects']['computer']['class'] == 'laptop'
    assert 'pi_command' in live and '--key testkey' in live['pi_command']
    assert c.get('/api/agents').json['lens']['name'] == 'Lens'
    assert c.get('/api/status').json['agents']
    assert c.get('/api/latest_frame_raw/0').status_code == 200
    assert c.get('/api/context').json['mention_options']


def test_chat_one_on_one_with_habit_action(setup):
    app, c = setup
    Feeder(app).run(4, [obj('cup', 410, 250, 450, 320)])
    r = c.post('/api/chat', json={'chat': 'cup', 'message': 'I just drank some water'}).json
    assert r['messages'][0]['character'] == 'cup' and '[LOG_SIP]' not in r['messages'][0]['text']
    assert r.get('habits_changed') and app.habits.d['sips'] == 1
    # the AI was told who it is and what's on the desk
    req = app.llm.client.requests[-1]
    system = req['messages'][0]['content']
    assert 'You are Cup' in system and 'LIVE DESK STATUS' in system and "USER'S WATER" in system


def test_group_chat_answers_with_agents_on_the_desk(setup):
    app, c = setup
    r = c.post('/api/chat', json={'chat': 'group', 'message': 'hello?'}).json
    assert r['messages'] == [] and 'Nobody is on the desk' in r['error']
    Feeder(app).run(4, [obj('laptop', 200, 200, 400, 350)])
    r = c.post('/api/chat', json={'chat': 'group', 'message': 'hello?'}).json
    assert r['messages'][0]['character'] == 'computer'


def test_lost_item_question(setup):
    app, c = setup
    Feeder(app).run(2, [obj('key', 50, 50, 80, 70)])
    r = c.post('/api/chat', json={'chat': 'group', 'message': 'have you seen my keys?'}).json
    assert r['lost_object_lookup'] and r['messages'][0]['cta']['action'] == 'captures'


def test_habits_endpoints(setup):
    app, c = setup
    assert c.post('/api/habits/log', json={'habit': 'hydration', 'action': 'glass'}).json['hydration']['water_ml'] == 240
    assert c.post('/api/habits/log', json={'habit': 'nope', 'action': 'x'}).status_code == 400
    today = c.get('/api/habits/today').json
    assert today['hydration']['goal_ml'] == 2400 and 'vision' in today
    assert c.get('/api/habit_log').json['summary']['breaks_today'] == 0


def test_preferences(setup):
    app, c = setup
    r = c.put('/api/preferences', json={'wake_time': '06:30', 'bed_time': '23:00', 'quiet_enabled': True}).json
    assert r['preferences']['wake_time'] == '06:30'
    assert c.put('/api/preferences', json={'wake_time': 'noon'}).status_code == 400
    # the old setting names still work
    assert c.put('/api/habit_policy', json={'dnd_enabled': False}).json['preferences']['quiet_enabled'] is False
    assert 'sunset_ts' in c.get('/api/preferences').json


def test_event_log_endpoints(setup):
    app, c = setup
    Feeder(app).run(5, [obj('laptop', 200, 200, 400, 350)])
    assert c.get('/api/log/days').json['days']
    assert c.get('/api/log/presence').json['intervals'][0]['label'] in ('laptop', 'person')
    assert any(e['type'] == 'agent_joined' for e in c.get('/api/log/events?types=agent_joined').json['events'])
    det = c.get('/api/log/detections?limit=1').json['detections'][0]
    assert det['type'] == 'detection' and det['detected_obj']
    status = c.get('/api/log/status').json
    assert status['today']['pictures_waiting'] == 15 and status['scheduler']
    assert c.post('/api/log/compact').json['frames'] == 15
    assert c.get('/api/log/status').json['today']['pictures_waiting'] == 0


def test_rules_and_desk_outline(setup):
    app, c = setup
    assert c.put('/api/rules', json={'desk_outline': [[0, 0], [1, 0]]}).status_code == 400
    assert c.put('/api/rules', json={'desk_outline': [[0, 0.5], [1, 0.5], [1, 1], [0, 1]]}).json['ok']
    rules = c.get('/api/rules').json
    assert len(rules['desk_outline']) == 4
    saved = json.loads(app.rules_path.read_text())
    assert saved['desk_outline'][0] == [0, 0.5] and 'danger_zones' in saved


def test_sse_stream_delivers_messages(setup):
    app, c = setup
    resp = c.get('/api/events', buffered=False)
    it = resp.response
    assert next(it).startswith(b'retry')
    app.generator.broadcast({'type': 'group_message', 'character': 'lens', 'text': 'hello from Lens'})
    data = next(it).decode()
    assert data.startswith('data: ') and 'hello from Lens' in data
    resp.close()


def test_snapshots(setup):
    app, c = setup
    Feeder(app).run(1, [])
    sid = app.desk.save_snapshot(label='Test')
    assert c.get(f'/api/snapshot/{sid}').status_code == 200
    assert c.get('/api/snapshots').json['items'][0]['label'] == 'Test'
    assert c.get('/api/snapshot/nope').status_code == 404
