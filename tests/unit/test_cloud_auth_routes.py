from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from desktalk_cloud.app import create_app
from desktalk_cloud.deps import SESSION_USER
from desktalk_cloud.domain import GoogleUser
from desktalk_cloud.google_login import HttpGoogleLogin
from desktalk_cloud.routers import auth
from tests.fakes.cloud import FakeGoogleLogin, cloud_config, make_parts

ANN = GoogleUser(sub='g-ann', email='ann@example.com', name='Ann', email_verified=True)
GOOGLE_SECRET = 'GOCSPX-never-in-a-log'


peek = APIRouter()


@peek.get('/_test/session')
def _session(request: Request):                     # what the login cookie holds, for the tests to look at
    return dict(request.session)


def build(parts) -> TestClient:
    return TestClient(create_app(parts, [auth.router, peek]), follow_redirects=False)


@pytest.fixture
def google():
    fake = FakeGoogleLogin()
    fake.people['ann-code'] = ANN
    return fake


@pytest.fixture
def parts(google):
    return make_parts(google=google)


@pytest.fixture
def client(parts):
    return build(parts)


def our_log(caplog) -> str:
    """What the server logged (not the test client's own request log, which shows the address it called)."""
    ours = [r for r in caplog.records if r.name.startswith('desktalk_cloud')]
    return '\n'.join(r.getMessage() + str(r.exc_info) + str(r.exc_text) for r in ours)


def start(client) -> str:
    """Go to the login address and give back the state Google was sent."""
    reply = client.get('/auth/google/login')
    assert reply.status_code == 302
    return parse_qs(urlparse(reply.headers['location']).query)['state'][0]


def test_login_sends_the_person_to_google_with_a_state_and_the_public_address(client, parts, google):
    reply = client.get('/auth/google/login')
    query = parse_qs(urlparse(reply.headers['location']).query)
    assert reply.headers['location'].startswith('https://google.test/login')
    assert query['redirect_uri'] == [parts.config.server.public_url.rstrip('/') + '/auth/google/callback']
    assert len(query['state'][0]) >= 20


def test_each_login_gets_a_new_state(client):
    assert start(client) != start(client)


def test_google_login_creates_user_and_cookie(client, parts):
    state = start(client)
    reply = client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state})
    assert reply.status_code == 303 and reply.headers['location'] == '/'
    assert parts.config.server.cookie_name in reply.headers['set-cookie']
    users = parts.store.list_users()
    assert [(u.google_sub, u.email, u.name) for u in users] == [('g-ann', 'ann@example.com', 'Ann')]
    assert client.get('/_test/session').json() == {SESSION_USER: users[0].id}


def test_the_redirect_goes_to_the_login_url_when_one_is_set(google):
    parts = make_parts(cloud_config(server={'login_url': 'http://localhost:5173/'}), google=google)
    client = build(parts)
    reply = client.get('/auth/google/callback', params={'code': 'ann-code', 'state': start(client)})
    assert reply.headers['location'] == 'http://localhost:5173/'


def test_a_second_login_by_the_same_person_keeps_one_user(client, parts, google):
    google.people['ann-code'] = GoogleUser(sub='g-ann', email='ann@example.com', name='Ann Lee', email_verified=True)
    for _ in range(2):
        client.get('/auth/google/callback', params={'code': 'ann-code', 'state': start(client)})
    assert [(u.google_sub, u.name) for u in parts.store.list_users()] == [('g-ann', 'Ann Lee')]


def test_wrong_state_refused(client, parts, google):
    start(client)
    reply = client.get('/auth/google/callback', params={'code': 'ann-code', 'state': 'not-the-state'})
    assert reply.status_code == 400 and reply.json()['detail'] == parts.config.messages.bad_state
    assert parts.store.list_users() == [] and google.exchanged == []


def test_no_state_in_the_url_or_no_login_started_is_refused(client, parts):
    assert client.get('/auth/google/callback', params={'code': 'ann-code'}).status_code == 400
    assert client.get('/auth/google/callback', params={'code': 'ann-code', 'state': 'x'}).status_code == 400
    assert parts.store.list_users() == []


def test_a_state_works_once(client, parts):
    state = start(client)
    assert client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state}).status_code == 303
    client.post('/auth/logout')
    assert client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state}).status_code == 400


def test_the_state_is_compared_in_constant_time(monkeypatch, client):
    seen = []
    real = auth.secrets.compare_digest
    monkeypatch.setattr(auth.secrets, 'compare_digest', lambda a, b: seen.append((a, b)) or real(a, b))
    client.get('/auth/google/callback', params={'code': 'c', 'state': start(client)})
    assert len(seen) == 1


def test_an_odd_state_does_not_crash(client):
    start(client)
    assert client.get('/auth/google/callback', params={'code': 'c', 'state': 'café %'}).status_code == 400
    assert client.get('/auth/google/callback?code=c&state=%ff%fe').status_code == 400


def test_google_saying_no_is_a_plain_error(client, parts):
    state = start(client)
    reply = client.get('/auth/google/callback', params={'error': 'access_denied', 'state': state})
    assert reply.status_code == 400 and reply.json()['detail'] == parts.config.messages.google_failed
    assert parts.store.list_users() == []


def test_a_wrong_code_is_a_plain_error(client, parts):
    reply = client.get('/auth/google/callback', params={'code': 'wrong', 'state': start(client)})
    assert reply.status_code == 502 and reply.json()['detail'] == parts.config.messages.google_failed
    assert parts.store.list_users() == []


def test_a_missing_code_is_a_plain_error(client, parts):
    reply = client.get('/auth/google/callback', params={'state': start(client)})
    assert reply.status_code == 400 and reply.json()['detail'] == parts.config.messages.google_failed


def test_unverified_email_refused(client, parts, google):
    google.people['ann-code'] = GoogleUser(sub='g-ann', email='ann@example.com', name='Ann', email_verified=False)
    reply = client.get('/auth/google/callback', params={'code': 'ann-code', 'state': start(client)})
    assert reply.status_code == 403 and reply.json()['detail'] == parts.config.messages.email_not_verified
    assert parts.store.list_users() == []
    assert client.get('/_test/session').json() == {}


def test_text_the_store_cannot_keep_is_refused(client, parts, google):
    google.people['ann-code'] = GoogleUser(sub='g-ann', email='ann@example.com', name='A\x00nn', email_verified=True)
    reply = client.get('/auth/google/callback', params={'code': 'ann-code', 'state': start(client)})
    assert reply.status_code == 502 and parts.store.list_users() == []


def test_no_google_settings_says_how_to_set_up():
    parts = make_parts(google=None)
    client = build(parts)
    for path in ('/auth/google/login', '/auth/google/callback'):
        reply = client.get(path, params={'code': 'c', 'state': 's'})
        assert reply.status_code == 503 and reply.json()['detail'] == parts.config.messages.google_not_set_up
    assert 'GOOGLE_CLIENT_ID' in parts.config.messages.google_not_set_up


def test_google_failure_shows_plain_error_and_logs_no_secret(caplog):
    leak = f'boom https://oauth2.googleapis.com/token?client_secret={GOOGLE_SECRET}&code=ann-code'
    broken = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError(leak)))
    google = HttpGoogleLogin(cloud_config().google, 'id.apps.test', SecretStr(GOOGLE_SECRET), transport=broken)
    parts = make_parts(google=google)
    client = build(parts)
    with caplog.at_level(logging.DEBUG):
        reply = client.get('/auth/google/callback', params={'code': 'ann-code', 'state': start(client)})
    assert reply.status_code == 502 and reply.json()['detail'] == parts.config.messages.google_failed
    shown = reply.text + our_log(caplog)
    assert GOOGLE_SECRET not in shown and 'ann-code' not in shown
    assert 'Google login failed' in our_log(caplog)


def test_no_state_code_or_cookie_value_reaches_a_log(client, caplog):
    with caplog.at_level(logging.DEBUG):
        state = start(client)
        client.get('/auth/google/callback', params={'code': 'ann-code', 'state': state})
        client.get('/auth/google/callback', params={'code': 'ann-code', 'state': 'forged-state'})
    cookie = client.cookies.get('desktalk_session')
    assert cookie
    shown = our_log(caplog)
    assert state not in shown and 'ann-code' not in shown and 'forged-state' not in shown and cookie not in shown


def test_logout_ends_the_session(client, parts):
    client.get('/auth/google/callback', params={'code': 'ann-code', 'state': start(client)})
    assert client.get('/_test/session').json() != {}
    assert client.post('/auth/logout').json() == {'ok': True}
    assert client.get('/_test/session').json() == {}


def test_logout_with_nobody_logged_in_is_fine(client):
    assert client.post('/auth/logout').status_code == 200
