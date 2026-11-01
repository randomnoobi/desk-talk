from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from desktalk_cloud.app import create_app
from desktalk_cloud.domain import GoogleUser
from desktalk_cloud.routers import auth, site
from desktalk_cloud.tokens import hash_secret, normal_code
from tests.fakes.cloud import FakeGoogleLogin, cloud_config, link_device, make_parts

WEEK = '2026-10-19'


@pytest.fixture
def parts():
    google = FakeGoogleLogin()
    for who in ('ann', 'bob'):
        google.people[f'{who}-code'] = GoogleUser(sub=f'google-{who}', email=f'{who}@example.com', name=who.title(),
                                                  email_verified=True)
    return make_parts(google=google)


@pytest.fixture
def app(parts):
    return create_app(parts, [auth.router, site.router])


def sign_in(app, who: str) -> TestClient:
    client = TestClient(app, follow_redirects=False)
    reply = client.get('/auth/google/login')
    state = parse_qs(urlparse(reply.headers['location']).query)['state'][0]
    assert client.get('/auth/google/callback', params={'code': f'{who}-code', 'state': state}).status_code == 303
    return client


@pytest.fixture
def ann(parts):
    return link_device(parts, 'ann')


@pytest.fixture
def bob(parts):
    return link_device(parts, 'bob')


@pytest.fixture
def ann_client(app, ann):
    return sign_in(app, 'ann')


@pytest.fixture
def bob_client(app, bob):
    return sign_in(app, 'bob')


def report_for(parts, device, week=WEEK, markdown='# Secret week'):
    report = parts.store.create_report(device.id, week, parts.clock.now())
    return parts.store.finish_report(report.id, 'done', markdown, 'fake-model', None, parts.clock.now())


# ── who you are ──────────────────────────────────────────────────────

def test_every_site_route_needs_a_login(app, parts, ann):
    client = TestClient(app, follow_redirects=False)
    report = report_for(parts, ann.device)
    calls = [('get', '/api/me'), ('delete', '/api/me'), ('get', '/api/devices'), ('post', '/api/devices/code'),
             ('patch', f'/api/devices/{ann.device.id}'), ('delete', f'/api/devices/{ann.device.id}'),
             ('delete', f'/api/devices/{ann.device.id}/data'), ('get', '/api/reports'),
             ('get', f'/api/reports/{report.id}')]
    for method, path in calls:
        reply = getattr(client, method)(path)
        assert reply.status_code == 401, (method, path)
        assert reply.json()['detail'] == parts.config.messages.not_logged_in
    assert parts.store.get_device(ann.device.id) is not None


def test_a_pis_token_does_not_open_the_site_routes(app, ann):
    client = TestClient(app)
    reply = client.get('/api/devices', headers={'Authorization': f'Bearer {ann.token}'})
    assert reply.status_code == 401


def test_me(ann_client, ann):
    body = ann_client.get('/api/me').json()
    assert body == {'id': ann.user.id, 'email': 'ann@example.com', 'name': 'Ann',
                    'created_at': ann.user.created_at}


def test_delete_me_removes_all(app, parts, ann, ann_client, bob, bob_client):
    report = report_for(parts, ann.device)
    parts.store.add_code(hash_secret('LEFTOVER'), ann.user.id, None, 'UTC', parts.clock.now() + 600)
    reply = ann_client.delete('/api/me')
    assert reply.status_code == 200 and reply.json() == {'ok': True}
    assert parts.store.get_user(ann.user.id) is None
    assert parts.store.get_device(ann.device.id) is None
    assert parts.store.get_report_by_id(report.id) is None
    assert parts.store.device_for_token(hash_secret(ann.token), parts.clock.now()) is None
    assert parts.store.claim_code(hash_secret('LEFTOVER'), 'x', hash_secret('t'), parts.clock.now()) is None
    assert ann_client.get('/api/me').status_code == 401                 # the session ended
    assert parts.store.get_user(bob.user.id) is not None                # nobody else was touched
    assert parts.store.get_device(bob.device.id) is not None
    assert bob_client.get('/api/me').status_code == 200


# ── Pis ──────────────────────────────────────────────────────────────

def test_devices_lists_only_my_own_including_removed(app, parts, ann, ann_client, bob):
    second = link_device(parts, 'ann')
    parts.store.remove_device(second.device.id, parts.clock.now())
    ids = {d['id']: d for d in ann_client.get('/api/devices').json()}
    assert set(ids) == {ann.device.id, second.device.id}
    assert bob.device.id not in ids
    assert ids[ann.device.id] == {'id': ann.device.id, 'name': ann.device.name, 'time_zone': 'UTC',
                                  'created_at': ann.device.created_at, 'last_seen_at': None, 'caught_up_at': None,
                                  'removed': False}
    assert ids[second.device.id]['removed'] is True


def test_code_reply_uses_public_url(parts, ann_client):
    reply = ann_client.post('/api/devices/code', json={'time_zone': 'Europe/London'})
    assert reply.status_code == 200
    body = reply.json()
    assert set(body) == {'code', 'expires_at', 'server_url', 'command'}
    assert body['server_url'] == parts.config.server.public_url
    assert body['expires_at'] == pytest.approx(parts.clock.now() + parts.config.pairing.code_valid_min * 60)
    assert body['code'] in body['command'] and body['server_url'] in body['command']
    assert 'link_cloud.py' in body['command']


def test_a_trailing_slash_in_the_public_url_is_not_doubled():
    google = FakeGoogleLogin()
    google.people['ann-code'] = GoogleUser(sub='g', email='a@example.com', name='A', email_verified=True)
    parts = make_parts(cloud_config(server={'public_url': 'http://192.168.1.9:8000/'}), google=google)
    client = sign_in(create_app(parts, [auth.router, site.router]), 'ann')
    body = client.post('/api/devices/code', json={'time_zone': 'UTC'}).json()
    assert body['server_url'] == 'http://192.168.1.9:8000'
    assert 'http://192.168.1.9:8000' in body['command']
    assert '8000/ ' not in body['command']


def test_a_code_made_on_the_website_links_a_new_pi_in_that_time_zone(parts, ann, ann_client):
    code = ann_client.post('/api/devices/code', json={'time_zone': 'Asia/Tokyo'}).json()['code']
    device = parts.store.claim_code(hash_secret(normal_code(code)), 'Desk', hash_secret('tok'), parts.clock.now())
    assert device is not None and device.id != ann.device.id
    assert device.user_id == ann.user.id and device.time_zone == 'Asia/Tokyo'


def test_the_code_is_stored_only_as_a_hash(parts, ann_client, caplog):
    with caplog.at_level(logging.DEBUG):
        code = ann_client.post('/api/devices/code', json={'time_zone': 'UTC'}).json()['code']
    assert code not in caplog.text
    raw = parts.store.claim_code(code, 'x', hash_secret('t'), parts.clock.now())
    assert raw is None                                       # the raw code isn't the key


def test_a_link_again_code_keeps_the_same_device(parts, ann, ann_client):
    parts.store.remove_device(ann.device.id, parts.clock.now())
    reply = ann_client.post('/api/devices/code', json={'time_zone': 'Asia/Tokyo', 'device_id': ann.device.id})
    assert reply.status_code == 200
    device = parts.store.claim_code(hash_secret(reply.json()['code']), 'ignored', hash_secret('tok'),
                                    parts.clock.now())
    assert device is not None and device.id == ann.device.id and device.removed_at is None


def test_code_for_other_persons_device_is_404(parts, ann, bob_client):
    reply = bob_client.post('/api/devices/code', json={'time_zone': 'UTC', 'device_id': ann.device.id})
    assert reply.status_code == 404 and reply.json()['detail'] == parts.config.messages.not_found


def test_code_for_an_unknown_device_is_404(parts, ann_client):
    assert ann_client.post('/api/devices/code', json={'time_zone': 'UTC', 'device_id': 'nope'}).status_code == 404
    assert ann_client.post('/api/devices/code', json={'time_zone': 'UTC', 'device_id': 'a\x00b'}).status_code == 404


@pytest.mark.parametrize('zone', ['Mars/Olympus', '', '   ', '../../etc/passwd', '/etc/passwd', 'a\x00b', 'UTC\n',
                                  'x' * 500, '..', '.', 'Europe', 5, None, ['UTC']])
def test_bad_time_zone_refused(parts, ann_client, zone):
    reply = ann_client.post('/api/devices/code', json={'time_zone': zone})
    assert reply.status_code == 400
    assert reply.json()['detail'] in (parts.config.messages.bad_time_zone, parts.config.messages.bad_body)


def test_the_time_zone_is_required(parts, ann_client):
    assert ann_client.post('/api/devices/code', json={}).status_code == 400


@pytest.mark.parametrize('body', ['[]', '"text"', 'null', 'not json', ''])
def test_a_code_request_that_is_not_an_object_is_refused(ann_client, body):
    reply = ann_client.post('/api/devices/code', content=body, headers={'content-type': 'application/json'})
    assert reply.status_code == 400


def test_a_device_id_that_is_not_text_is_refused(ann_client):
    assert ann_client.post('/api/devices/code', json={'time_zone': 'UTC', 'device_id': 5}).status_code == 400


def test_a_huge_code_request_is_refused(parts, ann_client):
    body = '{"time_zone": "UTC", "x": "' + 'a' * (parts.config.limits.claim_body_kb * 1024) + '"}'
    reply = ann_client.post('/api/devices/code', content=body, headers={'content-type': 'application/json'})
    assert reply.status_code == 413


def test_rename(parts, ann, ann_client):
    reply = ann_client.patch(f'/api/devices/{ann.device.id}', json={'name': '  Kitchen desk '})
    assert reply.status_code == 200 and reply.json()['name'] == 'Kitchen desk'
    assert parts.store.get_device(ann.device.id).name == 'Kitchen desk'


@pytest.mark.parametrize('body', [{'name': ''}, {'name': '   '}, {'name': 'x' * 61}, {'name': 5}, {}, {'name': 'a\x00'},
                                  [], 'x'])
def test_a_bad_name_is_refused(parts, ann, ann_client, body):
    reply = ann_client.patch(f'/api/devices/{ann.device.id}', json=body)
    assert reply.status_code == 400
    assert parts.store.get_device(ann.device.id).name == ann.device.name


def test_other_persons_device_cannot_be_renamed_removed_or_deleted(parts, ann, bob_client):
    path = f'/api/devices/{ann.device.id}'
    for reply in (bob_client.patch(path, json={'name': 'mine now'}), bob_client.delete(path),
                  bob_client.delete(path + '/data')):
        assert reply.status_code == 404 and reply.json()['detail'] == parts.config.messages.not_found
    device = parts.store.get_device(ann.device.id)
    assert device.name == ann.device.name and device.removed_at is None
    assert parts.store.device_for_token(hash_secret(ann.token), parts.clock.now()) is not None


def test_unknown_and_odd_device_ids_are_404(ann_client):
    for device_id in ('nope', 'a%00b', '%ff'):
        assert ann_client.patch(f'/api/devices/{device_id}', json={'name': 'x'}).status_code == 404
        assert ann_client.delete(f'/api/devices/{device_id}').status_code == 404
        assert ann_client.delete(f'/api/devices/{device_id}/data').status_code == 404


def test_remove_cancels_the_token_but_keeps_the_history(parts, ann, ann_client):
    report = report_for(parts, ann.device)
    reply = ann_client.delete(f'/api/devices/{ann.device.id}')
    assert reply.status_code == 200 and reply.json()['removed'] is True
    assert parts.store.device_for_token(hash_secret(ann.token), parts.clock.now()) is None
    assert parts.store.get_report_by_id(report.id) is not None
    assert ann_client.delete(f'/api/devices/{ann.device.id}').status_code == 200     # again: nothing changes


def test_delete_data_removes_the_pi_and_everything(parts, ann, ann_client):
    report = report_for(parts, ann.device)
    reply = ann_client.delete(f'/api/devices/{ann.device.id}/data')
    assert reply.status_code == 200 and reply.json() == {'ok': True}
    assert parts.store.get_device(ann.device.id) is None and parts.store.get_report_by_id(report.id) is None
    assert ann_client.delete(f'/api/devices/{ann.device.id}/data').status_code == 404


# ── reports ──────────────────────────────────────────────────────────

def test_reports_list_has_no_text_and_is_newest_first(parts, ann, ann_client):
    report_for(parts, ann.device, '2026-10-12')
    report_for(parts, ann.device, '2026-10-19')
    rows = ann_client.get('/api/reports', params={'device_id': ann.device.id}).json()
    assert [r['week_start'] for r in rows] == ['2026-10-19', '2026-10-12']
    assert set(rows[0]) == {'id', 'device_id', 'week_start', 'status', 'model', 'error', 'created_at'}
    assert 'Secret' not in str(rows)


def test_reports_without_a_device_lists_all_my_pis(parts, ann, ann_client, bob):
    second = link_device(parts, 'ann')
    report_for(parts, ann.device, '2026-10-12')
    report_for(parts, second.device, '2026-10-19')
    report_for(parts, bob.device, '2026-10-26')
    rows = ann_client.get('/api/reports').json()
    assert [(r['device_id'], r['week_start']) for r in rows] == [(second.device.id, '2026-10-19'),
                                                                  (ann.device.id, '2026-10-12')]


def test_reports_of_other_persons_device_is_404(parts, ann, bob_client):
    report_for(parts, ann.device)
    for device_id in (ann.device.id, 'nope', 'a\x00b'):
        assert bob_client.get('/api/reports', params={'device_id': device_id}).status_code == 404


def test_one_report_has_its_text(parts, ann, ann_client):
    report = report_for(parts, ann.device)
    body = ann_client.get(f'/api/reports/{report.id}').json()
    assert body['markdown'] == '# Secret week' and body['status'] == 'done' and body['model'] == 'fake-model'
    assert body['week_start'] == WEEK and body['device_id'] == ann.device.id


def test_other_persons_report_is_404(parts, ann, bob_client):
    report = report_for(parts, ann.device)
    reply = bob_client.get(f'/api/reports/{report.id}')
    assert reply.status_code == 404 and reply.json()['detail'] == parts.config.messages.not_found
    assert 'Secret' not in reply.text


def test_an_unknown_or_odd_report_id_is_404(ann_client):
    for report_id in ('nope', 'a%00b', '%ff'):
        assert ann_client.get(f'/api/reports/{report_id}').status_code == 404


def test_the_report_of_a_deleted_person_is_not_reachable_with_an_old_cookie(parts, ann, ann_client):
    report = report_for(parts, ann.device)
    parts.store.delete_user(ann.user.id)
    assert ann_client.get(f'/api/reports/{report.id}').status_code == 401


def test_the_site_never_shows_a_token_hash(parts, ann, ann_client):
    assert hash_secret(ann.token) not in ann_client.get('/api/devices').text
    assert ann.token not in ann_client.get('/api/devices').text
