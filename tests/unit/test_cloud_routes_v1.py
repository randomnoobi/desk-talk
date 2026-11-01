from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient

from desktalk_cloud.app import create_app
from desktalk_cloud.routers import devices_v1, events_v1
from tests.fakes.cloud import cloud_config, link_device, make_parts

login_router = APIRouter()


@login_router.get('/_test/login/{user_id}')
def _login(request: Request, user_id: str):         # stands in for Google login (Task 5), which sets this key
    request.session['user_id'] = user_id
    return {'ok': True}


def build(parts):
    return TestClient(create_app(parts, [devices_v1.router, events_v1.router, login_router]))


@pytest.fixture
def parts():
    return make_parts()


@pytest.fixture
def client(parts):
    return build(parts)


@pytest.fixture
def ann(parts):
    return link_device(parts, 'ann')


@pytest.fixture
def bob(parts):
    return link_device(parts, 'bob')


@pytest.fixture
def token(ann):
    return ann.token


@pytest.fixture
def other_token(bob):
    return bob.token


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def habit(event_id='h1', ts=100.0, **more):
    return {'id': event_id, 'ts': ts, 'type': 'habit', 'habit': 'water', 'action': 'sip', 'ml': 30, **more}


def post_events(client, token, events, caught_up=None, expect=200):
    body = {'events': events} if caught_up is None else {'events': events, 'caught_up': caught_up}
    response = client.post('/v1/events', json=body, headers=auth(token))
    assert response.status_code == expect, response.text
    return response.json()


def get_events(client, token, device_id, expect=200, **params):
    response = client.get(f'/v1/devices/{device_id}/events', params=params, headers=auth(token))
    assert response.status_code == expect, response.text
    return response.json()


# ── POST /v1/events ───────────────────────────────────────────────────────
def test_post_events_counts_accepted_repeats_refused(client, token, ann):
    first = post_events(client, token, [habit('a'), habit('b', 101.0), {'id': 'd', 'ts': 1.0, 'type': 'detection'}])
    assert first['accepted'] == 2 and first['repeats'] == 0
    assert first['refused'] == [{'id': 'd', 'why': first['refused'][0]['why']}] and first['refused'][0]['why']
    again = post_events(client, token, [habit('a'), habit('c', 102.0)])
    assert (again['accepted'], again['repeats'], again['refused']) == (1, 1, [])
    assert [e['id'] for e in get_events(client, token, ann.device.id)['events']] == ['a', 'b', 'c']


def test_one_bad_event_never_fails_the_batch(client, token, ann):
    bad_time = {'id': 'z', 'ts': 'x', 'type': 'habit'}
    reply = post_events(client, token, ['text', {'ts': 1}, habit('big', ml=10 ** 30), bad_time, habit('ok', 5.0)])
    assert reply['accepted'] == 1 and len(reply['refused']) == 4


def test_batch_over_limit_gets_413(parts, token):
    client = build(make_parts(cloud_config(ingest={'max_batch': 3}), store=parts.store, clock=parts.clock))
    post_events(client, token, [habit(str(i)) for i in range(3)])
    reply = client.post('/v1/events', json={'events': [habit(str(i)) for i in range(4)]}, headers=auth(token))
    assert reply.status_code == 413 and '3' in reply.json()['detail']


def test_body_over_limit_gets_413_even_without_content_length(parts, token):
    client = build(make_parts(cloud_config(ingest={'max_body_kb': 1}), store=parts.store, clock=parts.clock))

    def chunks():                                   # a streamed body has no Content-Length
        for _ in range(10):
            yield b'{"events": [], "pad": "' + b'x' * 400 + b'"}'

    reply = client.post('/v1/events', content=chunks(), headers=auth(token))
    assert reply.status_code == 413


def test_a_short_content_length_does_not_let_a_big_body_in(parts, token):
    client = build(make_parts(cloud_config(ingest={'max_body_kb': 1}), store=parts.store, clock=parts.clock))
    body = b'{"events": [], "pad": "' + b'x' * 5000 + b'"}'
    reply = client.post('/v1/events', content=body, headers={**auth(token), 'Content-Length': '20',
                                                              'Content-Type': 'application/json'})
    assert reply.status_code == 413


def test_declared_size_over_limit_gets_413_at_once(parts, token):
    client = build(make_parts(cloud_config(ingest={'max_body_kb': 1}), store=parts.store, clock=parts.clock))
    reply = client.post('/v1/events', json={'events': [habit(str(i)) for i in range(50)]}, headers=auth(token))
    assert reply.status_code == 413


@pytest.mark.parametrize('body', [b'not json', b'[1, 2]', b'{"events": 5}', b'{}', b'\xff\xfe', b'[' * 100000,
                                  b'{"events": [], "caught_up": "yes"}'])
def test_a_body_that_is_not_the_shape_gets_400(client, token, body):
    reply = client.post('/v1/events', content=body, headers=auth(token))
    assert reply.status_code == 400 and reply.json()['detail']


def test_server_filters_again(client, token, ann):
    post_events(client, token, [{'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'tasks', 'action': 'done',
                                 'say': 'essay'}])
    event = get_events(client, token, ann.device.id)['events'][0]
    assert 'say' not in event and event['habit'] == 'tasks'


def test_caught_up_and_last_seen_are_recorded(parts, client, token, ann):
    parts.clock.advance(100)
    post_events(client, token, [habit()], caught_up=True)
    device = parts.store.get_device(ann.device.id)
    assert device.last_seen_at == device.caught_up_at == parts.clock.now()


def test_removed_pi_gets_401(parts, client, token, ann):
    post_events(client, token, [habit()])
    parts.store.remove_device(ann.device.id, parts.clock.now())
    assert client.post('/v1/events', json={'events': []}, headers=auth(token)).status_code == 401
    assert client.get(f'/v1/devices/{ann.device.id}/state', headers=auth(token)).status_code == 401


@pytest.mark.parametrize('headers', [{}, {'Authorization': 'Bearer nope'}, {'Authorization': 'Basic abc'},
                                     {'Authorization': 'Bearer'}, {'Authorization': ''}])
def test_missing_or_wrong_token_gets_the_same_401(client, headers):
    reply = client.post('/v1/events', json={'events': []}, headers=headers)
    assert reply.status_code == 401
    assert reply.json() == client.post('/v1/events', json={'events': []}, headers={'Authorization': 'Bearer x'}).json()


# ── reading ───────────────────────────────────────────────────────────────
def test_other_persons_device_gets_404(client, token, other_token, ann, bob):
    post_events(client, token, [habit()])
    for path in ('state', 'events'):
        assert client.get(f'/v1/devices/{ann.device.id}/{path}', headers=auth(other_token)).status_code == 404
    assert client.get('/v1/devices/no-such-device/state', headers=auth(token)).status_code == 404
    assert get_events(client, other_token, bob.device.id)['events'] == []


def test_a_signed_in_person_reads_their_own_pi_only(client, ann, bob):
    assert client.get(f'/v1/devices/{ann.device.id}/state').status_code == 401            # no token, no cookie
    client.get(f'/_test/login/{ann.user.id}')
    assert client.get(f'/v1/devices/{ann.device.id}/state').status_code == 200
    assert client.get(f'/v1/devices/{bob.device.id}/state').status_code == 404
    assert client.get(f'/v1/devices/{bob.device.id}/events').status_code == 404


def test_a_signed_in_person_still_reads_a_removed_pi(parts, client, ann):
    parts.store.remove_device(ann.device.id, parts.clock.now())
    client.get(f'/_test/login/{ann.user.id}')
    assert client.get(f'/v1/devices/{ann.device.id}/state').json()['device']['removed'] is True


def test_a_cookie_for_a_deleted_person_gets_nothing(parts, client, ann):
    client.get(f'/_test/login/{ann.user.id}')
    parts.store.delete_user(ann.user.id)
    assert client.get(f'/v1/devices/{ann.device.id}/state').status_code == 401


def test_a_bad_token_does_not_fall_back_to_the_cookie(client, ann):
    client.get(f'/_test/login/{ann.user.id}')
    reply = client.get(f'/v1/devices/{ann.device.id}/state', headers=auth('wrong'))
    assert reply.status_code == 401


def test_paging_with_same_time_events_returns_all(client, token, ann):
    ids = [f'e{i}' for i in range(5)]
    post_events(client, token, [habit(i, 100.0) for i in ids] + [habit('later', 101.0)])
    seen, params = [], {'since': 0, 'limit': 2}
    for _ in range(10):
        page = get_events(client, token, ann.device.id, **params)
        seen += [e['id'] for e in page['events']]
        if not page['events']:
            break
        params = {'since': page['next_since'], 'after_id': page['next_after_id'], 'limit': 2}
    assert seen == ids + ['later']
    assert (page['next_since'], page['next_after_id']) == (101.0, 'later')           # an empty page keeps the place


def test_events_page_size_is_capped_by_settings(parts, token, ann):
    client = build(make_parts(cloud_config(ingest={'read_default_limit': 2, 'read_max_limit': 3}),
                              store=parts.store, clock=parts.clock))
    post_events(client, token, [habit(str(i), float(i)) for i in range(6)])
    assert len(get_events(client, token, ann.device.id)['events']) == 2
    assert len(get_events(client, token, ann.device.id, limit=100)['events']) == 3


@pytest.mark.parametrize('params', [{'limit': 0}, {'since': 'nan'}, {'since': 'inf'}, {'since': 'x'}])
def test_bad_query_values_are_refused(client, token, ann, params):
    reply = client.get(f'/v1/devices/{ann.device.id}/events', params=params, headers=auth(token))
    assert reply.status_code in (400, 422)


def test_state_has_goals_last_seen_camera_and_objects(parts, client, token, ann):
    parts.clock.advance(500)
    goals = {'id': 'goals-5', 'ts': 5.0, 'type': 'goals', 'wake': '07:00', 'bed': '23:00', 'goals': {'water_ml': 2000}}
    camera = {'id': 'cam1', 'ts': 6.0, 'type': 'camera', 'key': 'covered', 'kind': 'warning'}
    cup = {'id': 'o1', 'ts': 7.0, 'type': 'object_state', 'change': 'moved', 'object': 'cup', 'prev_state': 'empty',
           'new_state': 'full'}
    old_cup = {'id': 'o0', 'ts': 3.0, 'type': 'object_state', 'change': 'moved', 'object': 'cup',
               'prev_state': 'x', 'new_state': 'empty'}
    post_events(client, token, [goals, camera, cup, old_cup])
    state = client.get(f'/v1/devices/{ann.device.id}/state', headers=auth(token)).json()
    assert state['goals']['wake'] == '07:00' and state['goals']['goals'] == {'water_ml': 2000}
    assert state['last_camera']['key'] == 'covered'
    assert state['last_seen_at'] == parts.clock.now()
    assert state['objects'] == [{'label': 'cup', 'state': 'full', 'since': 7.0, 'updated_at': parts.clock.now()}]
    assert state['device'] == {'id': ann.device.id, 'name': "ann's Pi", 'time_zone': 'UTC', 'removed': False}


def test_state_of_a_new_pi_is_empty(client, token, ann):
    state = client.get(f'/v1/devices/{ann.device.id}/state', headers=auth(token)).json()
    assert (state['goals'], state['last_camera'], state['last_seen_at'], state['objects']) == (None, None, None, [])


# ── POST /v1/devices/claim ────────────────────────────────────────────────
def make_code(parts, user, *, minutes=10, device_id=None, time_zone='Europe/Paris'):
    from desktalk_cloud.tokens import hash_secret, new_code
    code = new_code()
    parts.store.add_code(hash_secret(code), user.id, device_id, time_zone, parts.clock.now() + minutes * 60)
    return code


def test_claim_gives_the_device_and_a_working_token(parts, client, ann):
    code = make_code(parts, ann.user)
    reply = client.post('/v1/devices/claim', json={'code': code, 'name': 'Desk Pi'})
    assert reply.status_code == 200
    device_id, new_token = reply.json()['device_id'], reply.json()['token']
    device = parts.store.get_device(device_id)
    assert (device.name, device.time_zone, device.user_id) == ('Desk Pi', 'Europe/Paris', ann.user.id)
    assert post_events(client, new_token, [habit()])['accepted'] == 1
    assert client.post('/v1/devices/claim', json={'code': code, 'name': 'Again'}).status_code == 400    # once only


def test_claim_accepts_a_code_typed_in_small_letters_with_a_dash(parts, client, ann):
    code = make_code(parts, ann.user)
    typed = (code[:4] + '-' + code[4:]).lower()
    assert client.post('/v1/devices/claim', json={'code': typed, 'name': 'Pi'}).status_code == 200


def test_link_again_keeps_the_device(parts, client, ann):
    code = make_code(parts, ann.user, device_id=ann.device.id)
    reply = client.post('/v1/devices/claim', json={'code': code, 'name': 'Renamed on the Pi'}).json()
    assert reply['device_id'] == ann.device.id and reply['token'] != ann.token
    assert client.post('/v1/events', json={'events': []}, headers=auth(ann.token)).status_code == 401
    assert client.post('/v1/events', json={'events': []}, headers=auth(reply['token'])).status_code == 200


def test_claim_wrong_and_expired_code_same_reply(parts, client, ann):
    expired = make_code(parts, ann.user, minutes=10)
    parts.clock.advance(11 * 60)
    wrong = client.post('/v1/devices/claim', json={'code': 'WRONGONE', 'name': 'Pi'})
    old = client.post('/v1/devices/claim', json={'code': expired, 'name': 'Pi'})
    assert wrong.status_code == old.status_code == 400
    assert wrong.json() == old.json() and wrong.json()['detail']


@pytest.mark.parametrize('body', [{}, {'code': 5, 'name': 'x'}, {'code': 'ABCDEFGH', 'name': 5},
                                  {'code': 'ABCDEFGH', 'name': ''}, {'code': 'ABCDEFGH', 'name': 'x' * 1000},
                                  [1], {'code': ''}])
def test_claim_with_a_bad_body_gets_400(client, body):
    assert client.post('/v1/devices/claim', json=body).status_code == 400


def test_claim_without_a_name_uses_the_default_name(parts, client, ann):
    code = make_code(parts, ann.user)
    reply = client.post('/v1/devices/claim', json={'code': code})
    assert parts.store.get_device(reply.json()['device_id']).name == parts.config.messages.default_pi_name


def test_claim_attempts_are_limited(parts, ann):
    client = build(make_parts(cloud_config(limits={'claim_tries': 3}), store=parts.store, clock=parts.clock))
    replies = [client.post('/v1/devices/claim', json={'code': f'WRONG{i}XX', 'name': 'Pi'}) for i in range(5)]
    assert [r.status_code for r in replies] == [400, 400, 400, 429, 429]
    code = make_code(parts, ann.user)
    assert client.post('/v1/devices/claim', json={'code': code, 'name': 'Pi'}).status_code == 429      # even a good one
    parts.clock.advance(parts.config.limits.claim_window_sec + 1)
    fresh = make_code(parts, ann.user)
    assert client.post('/v1/devices/claim', json={'code': fresh, 'name': 'Pi'}).status_code == 200


def test_token_and_code_never_logged(parts, client, ann, caplog):
    caplog.set_level(logging.DEBUG)
    code = make_code(parts, ann.user)
    reply = client.post('/v1/devices/claim', json={'code': code, 'name': 'Pi'}).json()
    client.post('/v1/devices/claim', json={'code': 'WRONGONE', 'name': 'Pi'})
    client.post('/v1/events', json={'events': [habit(say='secret-todo-text')]}, headers=auth(reply['token']))
    client.post('/v1/events', json={'events': []}, headers=auth('bad-token-value'))
    client.get(f'/v1/devices/{reply["device_id"]}/state', headers=auth(reply['token']))
    text = caplog.text
    assert text
    for secret in (code, reply['token'], 'bad-token-value', 'WRONGONE', 'secret-todo-text', ann.token):
        assert secret not in text


def test_the_session_cookie_is_signed_and_lax(client, ann):
    reply = client.get(f'/_test/login/{ann.user.id}')
    cookie = reply.headers['set-cookie']
    assert 'samesite=lax' in cookie.lower() and 'httponly' in cookie.lower()
    assert 'secure' not in cookie.lower()


def test_https_only_marks_the_cookie_secure(parts, ann):
    client = TestClient(create_app(make_parts(cloud_config(server={'https_only': True}), store=parts.store),
                                   [login_router]), base_url='https://testserver')
    assert 'secure' in client.get(f'/_test/login/{ann.user.id}').headers['set-cookie'].lower()


def test_healthz(client):
    assert client.get('/healthz').json() == {'ok': True}
