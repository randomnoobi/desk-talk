from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from desktalk_cloud.google_login import HttpGoogleLogin
from desktalk_cloud.ports import GoogleFailed
from tests.fakes.cloud import cloud_config

CLIENT_ID = 'the-client-id.apps.test'
CLIENT_SECRET = 'GOCSPX-super-secret-value'
REDIRECT = 'http://localhost:8000/auth/google/callback'
SETTINGS = cloud_config().google


class Google:
    """A pretend Google: records what it was asked and answers from `token_reply` and `info_reply`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_reply: httpx.Response | Exception = httpx.Response(200, json={'access_token': 'at-123'})
        self.info_reply: httpx.Response | Exception = httpx.Response(
            200, json={'sub': '1001', 'email': 'ann@example.com', 'name': 'Ann', 'email_verified': True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.token_reply if str(request.url) == SETTINGS.token_url else self.info_reply
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def google():
    return Google()


@pytest.fixture
def login(google):
    return HttpGoogleLogin(SETTINGS, CLIENT_ID, SecretStr(CLIENT_SECRET), transport=httpx.MockTransport(google))


def test_login_url_has_what_google_needs(login):
    url = urlparse(login.login_url('state-1', REDIRECT))
    query = parse_qs(url.query)
    assert f'{url.scheme}://{url.netloc}{url.path}' == SETTINGS.auth_url
    assert query['client_id'] == [CLIENT_ID]
    assert query['redirect_uri'] == [REDIRECT]
    assert query['state'] == ['state-1']
    assert query['response_type'] == ['code']
    assert query['scope'] == [SETTINGS.scopes]
    assert 'client_secret' not in query


def test_exchange_trades_the_code_then_asks_who_it_is(login, google):
    person = login.exchange('code-1', REDIRECT)
    assert (person.sub, person.email, person.name, person.email_verified) == ('1001', 'ann@example.com', 'Ann', True)
    token_request, info_request = google.requests
    assert str(token_request.url) == SETTINGS.token_url and token_request.method == 'POST'
    form = parse_qs(token_request.content.decode())
    assert form['code'] == ['code-1'] and form['redirect_uri'] == [REDIRECT]
    assert form['client_id'] == [CLIENT_ID] and form['client_secret'] == [CLIENT_SECRET]
    assert form['grant_type'] == ['authorization_code']
    assert str(info_request.url) == SETTINGS.userinfo_url
    assert info_request.headers['authorization'] == 'Bearer at-123'


def test_a_missing_name_falls_back_to_the_email(login, google):
    google.info_reply = httpx.Response(200, json={'sub': '1', 'email': 'a@example.com', 'email_verified': True})
    assert login.exchange('c', REDIRECT).name == 'a@example.com'


@pytest.mark.parametrize('said, expected', [(True, True), ('true', True), (False, False), ('false', False),
                                            (None, False), ('yes', False), (1, False)])
def test_email_verified_is_true_only_when_google_says_so(login, google, said, expected):
    google.info_reply = httpx.Response(200, json={'sub': '1', 'email': 'a@example.com', 'email_verified': said})
    assert login.exchange('c', REDIRECT).email_verified is expected


def test_a_missing_email_verified_means_not_verified(login, google):
    google.info_reply = httpx.Response(200, json={'sub': '1', 'email': 'a@example.com'})
    assert login.exchange('c', REDIRECT).email_verified is False


@pytest.mark.parametrize('reply', [
    httpx.Response(400, json={'error': 'invalid_grant'}),
    httpx.Response(500, text='oops'),
    httpx.Response(200, text='not json'),
    httpx.Response(200, json=['a list']),
    httpx.Response(200, json={}),
    httpx.Response(200, json={'access_token': ''}),
    httpx.Response(200, json={'access_token': 5}),
    httpx.Response(302, headers={'location': 'https://evil.test/'}),
])
def test_a_bad_token_reply_is_a_google_failure(login, google, reply):
    google.token_reply = reply
    with pytest.raises(GoogleFailed):
        login.exchange('c', REDIRECT)
    assert len(google.requests) == 1                    # it never asked who the person is, nor followed a redirect


@pytest.mark.parametrize('reply', [
    httpx.Response(401, json={'error': 'bad'}),
    httpx.Response(200, text='<html>'),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={'email': 'a@example.com'}),                       # no sub
    httpx.Response(200, json={'sub': '', 'email': 'a@example.com'}),
    httpx.Response(200, json={'sub': 5, 'email': 'a@example.com'}),
    httpx.Response(200, json={'sub': '1'}),                                     # no email
    httpx.Response(200, json={'sub': '1', 'email': 7}),
    httpx.Response(200, json={'sub': '1', 'email': 'a@example.com', 'name': 7}),
])
def test_a_bad_userinfo_reply_is_a_google_failure(login, google, reply):
    google.info_reply = reply
    with pytest.raises(GoogleFailed):
        login.exchange('c', REDIRECT)


@pytest.mark.parametrize('which', ['token_reply', 'info_reply'])
@pytest.mark.parametrize('error', [httpx.ConnectError('x'), httpx.ReadTimeout('x'), httpx.InvalidURL('x')])
def test_network_trouble_is_a_google_failure(login, google, which, error):
    setattr(google, which, error)
    with pytest.raises(GoogleFailed):
        login.exchange('c', REDIRECT)


def test_the_failure_holds_no_secret_even_when_httpx_does(login, google):
    leak = f'cannot reach {SETTINGS.token_url}?client_secret={CLIENT_SECRET}&code=code-77 with at-123'
    google.token_reply = httpx.ConnectError(leak)
    with pytest.raises(GoogleFailed) as caught:
        login.exchange('code-77', REDIRECT)
    text = str(caught.value)
    assert CLIENT_SECRET not in text and 'code-77' not in text and 'at-123' not in text
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_the_access_token_is_not_in_a_failure_from_the_second_call(login, google):
    google.info_reply = httpx.ReadTimeout(f'timed out with Bearer at-123 {CLIENT_SECRET}')
    with pytest.raises(GoogleFailed) as caught:
        login.exchange('c', REDIRECT)
    assert 'at-123' not in str(caught.value) and CLIENT_SECRET not in str(caught.value)


def test_the_client_secret_is_not_in_its_repr(login):
    assert CLIENT_SECRET not in repr(login) and CLIENT_SECRET not in str(vars(login))
