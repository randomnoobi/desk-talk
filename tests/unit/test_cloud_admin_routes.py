from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from desktalk_cloud.admins import ensure_admin, hash_password, verify_password
from desktalk_cloud.app import create_app
from desktalk_cloud.domain import FAILED, GoogleUser
from desktalk_cloud.routers import admin, auth, site
from desktalk_cloud.store_memory import MemoryStore
from tests.fakes.clock import FakeClock
from tests.fakes.cloud import FakeGoogleLogin, cloud_config, link_device, make_parts

ENV = {'ADMIN_USERNAME': 'boss', 'ADMIN_PASSWORD': 'a long admin password'}
LOGIN = {'username': 'boss', 'password': 'a long admin password'}


class ForgetfulStore(MemoryStore):
    """A store that can lose its admin, as if the account were deleted."""

    forget = False

    def get_admin(self, username):
        return None if self.forget else super().get_admin(username)


def build(parts) -> TestClient:
    ensure_admin(parts.config, parts.store)
    return TestClient(create_app(parts, [auth.router, site.router, admin.router]), follow_redirects=False,
                      base_url=parts.config.server.public_url)


@pytest.fixture
def google():
    fake = FakeGoogleLogin()
    fake.people['ann-code'] = GoogleUser(sub='google-ann', email='ann@example.com', name='Ann', email_verified=True)
    return fake


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def parts(google, clock):
    return make_parts(cloud_config(ENV), clock=clock, google=google)


@pytest.fixture
def client(parts):
    return build(parts)


@pytest.fixture
def boss(client):
    assert client.post('/auth/admin/login', json=LOGIN).status_code == 200
    return client


def sign_in_as_ann(client) -> None:
    reply = client.get('/auth/google/login')
    state = parse_qs(urlparse(reply.headers['location']).query)['state'][0]
    assert client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state}).status_code == 303


# ── the account ──────────────────────────────────────────────────────

def test_admin_password_is_hashed_and_login_works(client, parts):
    saved = parts.store.get_admin('boss')
    assert saved is not None and saved.password_hash.startswith('$argon2')
    assert 'a long admin password' not in saved.password_hash
    reply = client.post('/auth/admin/login', json=LOGIN)
    assert reply.status_code == 200 and reply.json() == {'ok': True}
    assert client.get('/api/admin/overview').status_code == 200


def test_ensure_admin_makes_it_once_and_keeps_an_existing_one(parts):
    store = MemoryStore()
    assert ensure_admin(parts.config, store) is True
    first = store.get_admin('boss')
    assert ensure_admin(parts.config, store) is False
    assert store.get_admin('boss') == first


def test_a_changed_password_in_env_is_warned_about_without_showing_it(parts, caplog):
    store = MemoryStore()
    ensure_admin(parts.config, store)
    changed = cloud_config({**ENV, 'ADMIN_PASSWORD': 'a brand new password here'})
    with caplog.at_level(logging.DEBUG):
        assert ensure_admin(changed, store) is False
    assert 'ADMIN_PASSWORD' in caplog.text
    assert 'a brand new password here' not in caplog.text and 'a long admin password' not in caplog.text


@pytest.mark.parametrize('env', [{}, {'ADMIN_USERNAME': 'boss'}, {'ADMIN_PASSWORD': 'only a password'}])
def test_no_admin_settings_means_no_admin_login(env, google, caplog):
    parts = make_parts(cloud_config(env), google=google)
    with caplog.at_level(logging.DEBUG):
        assert ensure_admin(parts.config, parts.store) is False
    assert parts.store.get_admin('boss') is None
    client = build(parts)
    reply = client.post('/auth/admin/login', json=LOGIN)
    assert reply.status_code == 404 and reply.json()['detail'] == parts.config.messages.admin_not_set_up
    assert 'only a password' not in caplog.text


def test_an_admin_already_saved_cannot_log_in_once_the_settings_are_gone(parts, google):
    build(parts)                                             # made the admin
    without = make_parts(cloud_config(), store=parts.store, google=google)
    client = build(without)
    assert client.post('/auth/admin/login', json=LOGIN).status_code == 404


def test_the_hash_check(parts):
    hashed = hash_password('secret one')
    assert verify_password(hashed, 'secret one') is True
    assert verify_password(hashed, 'secret two') is False
    assert verify_password('not a hash', 'secret one') is False
    assert hash_password('secret one') != hashed                # a new salt each time


def test_wrong_password_and_wrong_username_get_the_same_answer(client, parts):
    reasons = set()
    for body in ({'username': 'boss', 'password': 'nope'}, {'username': 'nobody', 'password': 'nope'},
                 {'username': 'BOSS', 'password': LOGIN['password']}):
        reply = client.post('/auth/admin/login', json=body)
        assert reply.status_code == 401
        reasons.add(reply.json()['detail'])
    assert reasons == {parts.config.messages.admin_login_failed}
    assert client.get('/api/admin/overview').status_code == 401


@pytest.mark.parametrize('body', [{}, {'username': 'boss'}, {'password': 'x'}, {'username': 5, 'password': 'x'},
                                  {'username': 'boss', 'password': ['x']}, {'username': 'b\x00', 'password': 'x'},
                                  [], 'text', None])
def test_a_bad_login_body_is_refused(client, body):
    reply = client.post('/auth/admin/login', content=json.dumps(body), headers={'content-type': 'application/json'})
    assert reply.status_code == 400


def test_a_huge_login_body_is_refused(client, parts):
    body = '{"username": "boss", "password": "' + 'a' * (parts.config.limits.claim_body_kb * 1024) + '"}'
    reply = client.post('/auth/admin/login', content=body, headers={'content-type': 'application/json'})
    assert reply.status_code == 413


def test_admin_login_attempts_are_limited(google, clock):
    parts = make_parts(cloud_config(ENV, limits={'claim_tries': 3}), clock=clock, google=google)
    client = build(parts)
    wrong = {'username': 'boss', 'password': 'nope'}
    assert [client.post('/auth/admin/login', json=wrong).status_code for _ in range(3)] == [401, 401, 401]
    reply = client.post('/auth/admin/login', json=LOGIN)                  # even the right password, now
    assert reply.status_code == 429 and reply.json()['detail'] == parts.config.messages.too_many_tries
    assert client.get('/api/admin/overview').status_code == 401
    clock.advance(parts.config.limits.claim_window_sec + 1)
    assert client.post('/auth/admin/login', json=LOGIN).status_code == 200


def test_admin_tries_and_pi_code_tries_are_counted_apart(google):
    parts = make_parts(cloud_config(ENV, limits={'claim_tries': 2}), google=google)
    client = build(parts)
    assert [parts.claim_limiter.hit('testclient') for _ in range(3)] == [True, True, False]    # a Pi's tries ran out
    assert client.post('/auth/admin/login', json=LOGIN).status_code == 200


def test_the_password_is_never_logged(client, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post('/auth/admin/login', json={'username': 'boss', 'password': 'guess-123-never-log'})
        client.post('/auth/admin/login', json=LOGIN)
    assert 'guess-123-never-log' not in caplog.text and LOGIN['password'] not in caplog.text


# ── who can reach what ───────────────────────────────────────────────

def test_a_visitor_cannot_reach_admin_routes(client, parts):
    assert client.get('/api/admin/overview').status_code == 401
    assert client.post('/api/admin/reports/x/rerun', json={}).status_code == 401


def test_person_cannot_reach_admin_routes(client, parts):
    sign_in_as_ann(client)
    assert client.get('/api/me').status_code == 200
    assert client.get('/api/admin/overview').status_code == 401
    assert client.post('/api/admin/reports/x/rerun', json={}).status_code == 401


def test_the_admin_cookie_is_not_a_persons_login(boss):
    assert boss.get('/api/me').status_code == 401
    assert boss.get('/api/devices').status_code == 401


def test_a_person_and_the_admin_in_one_browser_stay_apart(boss):
    sign_in_as_ann(boss)
    assert boss.get('/api/me').json()['email'] == 'ann@example.com'
    assert boss.get('/api/admin/overview').status_code == 200


def test_logout_ends_the_admin_session(boss):
    assert boss.post('/auth/logout', json={}).status_code == 200
    assert boss.get('/api/admin/overview').status_code == 401


def test_a_session_for_an_admin_that_is_gone_is_refused(google):
    store = ForgetfulStore()
    client = build(make_parts(cloud_config(ENV), store=store, google=google))
    assert client.post('/auth/admin/login', json=LOGIN).status_code == 200
    store.forget = True
    assert client.get('/api/admin/overview').status_code == 401


# ── what the admin sees ──────────────────────────────────────────────

def test_overview_has_no_events_or_report_text(boss, parts):
    ann = link_device(parts, 'ann')
    bob = link_device(parts, 'bob')
    now = parts.clock.now()
    parts.store.save_batch(ann.device.id, [{'id': 'e1', 'ts': now, 'type': 'habit', 'habit': 'hydration',
                                            'action': 'EVENT-SECRET'}], [], [], True, now)
    done = parts.store.create_report(ann.device.id, '2026-10-12', now)
    parts.store.finish_report(done.id, 'done', '# REPORT-SECRET', 'fake', None, now)
    bad = parts.store.create_report(bob.device.id, '2026-10-19', now)
    parts.store.finish_report(bad.id, 'failed', None, None, 'No key is set', now)
    parts.store.remove_device(bob.device.id, now)

    reply = boss.get('/api/admin/overview')
    assert reply.status_code == 200
    body = reply.json()
    assert 'EVENT-SECRET' not in reply.text and 'REPORT-SECRET' not in reply.text and 'markdown' not in reply.text
    assert set(body) == {'users', 'devices', 'failed_reports', 'recent_reports'}
    assert {u['email'] for u in body['users']} == {'ann@example.com', 'bob@example.com'}
    by_id = {d['id']: d for d in body['devices']}
    assert by_id[ann.device.id]['last_seen_at'] == now and by_id[ann.device.id]['removed'] is False
    assert by_id[ann.device.id]['user_id'] == ann.user.id
    assert by_id[bob.device.id]['removed'] is True
    assert [(r['id'], r['status'], r['error']) for r in body['failed_reports']] == [(bad.id, 'failed', 'No key is set')]
    assert body['failed_reports'][0]['device_id'] == bob.device.id
    assert {u['id']: u['device_count'] for u in body['users']} == {ann.user.id: 1, bob.user.id: 1}


def test_overview_never_shows_a_token_or_a_hash(boss, parts):
    linked = link_device(parts, 'ann')
    text = boss.get('/api/admin/overview').text
    assert linked.token not in text and 'argon2' not in text and 'password' not in text.lower()


def test_rerun_resets_the_report(boss, parts, clock):
    ann = link_device(parts, 'ann')
    now = parts.clock.now()
    report = parts.store.create_report(ann.device.id, '2026-10-12', now)
    parts.store.finish_report(report.id, 'failed', None, None, 'boom', now)
    parts.store.retry_report(report.id, 'boom', now + 5)
    clock.advance(100)
    reply = boss.post(f'/api/admin/reports/{report.id}/rerun', json={})
    assert reply.status_code == 200
    body = reply.json()
    assert body['id'] == report.id and body['status'] == 'pending' and body['error'] is None
    assert 'markdown' not in body
    fresh = parts.store.get_report_by_id(report.id)
    assert (fresh.status, fresh.tries, fresh.error, fresh.markdown) == ('pending', 0, None, None)
    assert fresh.created_at == now          # a re-run keeps its first time, so the wait for the Pi isn't started again
    assert [r.id for r in parts.store.reports_to_write(parts.clock.now())] == [report.id]


def test_rerun_of_a_finished_report_clears_its_text(boss, parts):
    ann = link_device(parts, 'ann')
    report = parts.store.create_report(ann.device.id, '2026-10-12', parts.clock.now())
    parts.store.finish_report(report.id, 'done', '# old', 'm', None, parts.clock.now())
    assert boss.post(f'/api/admin/reports/{report.id}/rerun', json={}).status_code == 200
    assert parts.store.get_report_by_id(report.id).markdown is None


@pytest.mark.parametrize('report_id', ['nope', 'a%00b', '%ff'])
def test_rerun_of_an_unknown_report_is_404(boss, report_id):
    assert boss.post(f'/api/admin/reports/{report_id}/rerun', json={}).status_code == 404


# ── a JSON content type on the routes that change things ─────────────

@pytest.mark.parametrize('headers', [{}, {'content-type': 'text/plain'}])
def test_admin_login_and_rerun_need_a_json_content_type(client, parts, boss, headers):
    ann = link_device(parts, 'ann')
    report = parts.store.create_report(ann.device.id, '2026-10-12', parts.clock.now())
    parts.store.finish_report(report.id, 'done', '# keep me', 'm', None, parts.clock.now())
    rerun = boss.post(f'/api/admin/reports/{report.id}/rerun', content=b'{}', headers=headers)
    assert rerun.status_code == 415 and rerun.json()['detail'] == parts.config.prompts['site']['needs_json']
    assert parts.store.get_report_by_id(report.id).status == 'done'
    fresh = build(make_parts(cloud_config(ENV), google=None))
    reply = fresh.post('/auth/admin/login', content=b'{"username": "boss", "password": "x"}', headers=headers)
    assert reply.status_code == 415
    assert fresh.get('/api/admin/overview').status_code == 401


def test_a_wrong_content_type_does_not_use_up_a_login_try(google):
    parts = make_parts(cloud_config(ENV, limits={'claim_tries': 1}), google=google)
    client = build(parts)
    assert client.post('/auth/admin/login', content=b'x', headers={'content-type': 'text/plain'}).status_code == 415
    assert client.post('/auth/admin/login', json=LOGIN).status_code == 200


def test_the_unknown_username_check_does_not_make_a_hash_on_the_spot(monkeypatch, parts):
    from desktalk_cloud import admins

    def no_new_hash(password):
        raise AssertionError('the stand-in hash should have been made up front')
    monkeypatch.setattr(admins, 'hash_password', no_new_hash)
    assert admins.admin_login_ok(parts.config, parts.store, 'nobody', 'x') is False


def test_overview_lists_the_latest_finished_report_of_each_pi_without_text(boss, parts):
    ann = link_device(parts, 'ann')
    bob = link_device(parts, 'bob')
    now = parts.clock.now()
    for week, status, text in (('2026-10-05', 'done', '# OLD-SECRET'), ('2026-10-12', 'no_data', None),
                               ('2026-10-19', 'done', '# NEW-SECRET')):
        report = parts.store.create_report(ann.device.id, week, now)
        parts.store.finish_report(report.id, status, text, 'm', None, now)
    pending = parts.store.create_report(ann.device.id, '2026-10-26', now)              # not finished: not listed
    bad = parts.store.create_report(bob.device.id, '2026-10-19', now)
    parts.store.finish_report(bad.id, 'failed', None, None, 'boom', now)               # failed: in failed_reports only
    reply = boss.get('/api/admin/overview')
    rows = reply.json()['recent_reports']
    assert [(r['device_id'], r['week_start'], r['status']) for r in rows] == [(ann.device.id, '2026-10-19', 'done')]
    assert set(rows[0]) == {'id', 'device_id', 'week_start', 'status', 'model', 'error', 'tries', 'created_at'}
    assert 'SECRET' not in reply.text and pending.id not in reply.text
    # and the row's id is what re-run takes
    assert boss.post(f'/api/admin/reports/{rows[0]["id"]}/rerun', json={}).status_code == 200


def test_rerun_of_a_removed_pis_report_is_refused_in_plain_words(boss, parts, clock):
    ann = link_device(parts, 'ann')
    report = parts.store.create_report(ann.device.id, '2026-10-19', clock.now())
    parts.store.remove_device(ann.device.id, clock.now())
    parts.store.finish_report(report.id, FAILED, None, None, 'gone', clock.now())
    reply = boss.post(f'/api/admin/reports/{report.id}/rerun', json={})
    assert reply.status_code == 409 and reply.json()['detail'] == parts.config.messages.report_pi_removed
    assert parts.store.get_report_by_id(report.id).status == FAILED          # left as it was


def test_the_overview_does_not_load_report_text(boss, parts, clock, monkeypatch):
    ann = link_device(parts, 'ann')
    parts.store.create_report(ann.device.id, '2026-10-19', clock.now())
    asked: list[bool] = []
    real = parts.store.list_reports

    def spy(device_id, *, with_text=True):
        asked.append(with_text)
        return real(device_id, with_text=with_text)

    monkeypatch.setattr(parts.store, 'list_reports', spy)
    assert boss.get('/api/admin/overview').status_code == 200
    assert asked == [False]
