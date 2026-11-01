"""What every GoogleLogin must do (desktalk_cloud.ports.GoogleLogin). The fake in tests/fakes/cloud.py and the real
HttpGoogleLogin (against a pretend Google) run the same tests."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from desktalk_cloud.domain import GoogleUser
from desktalk_cloud.google_login import HttpGoogleLogin
from desktalk_cloud.ports import GoogleFailed, GoogleLogin
from tests.fakes.cloud import FakeGoogleLogin, cloud_config

REDIRECT = 'http://localhost:8000/auth/google/callback'
ANN = GoogleUser(sub='1001', email='ann@example.com', name='Ann', email_verified=True)


class GoogleLoginContract:
    """Subclass this and give two fixtures:

    - `login`: a GoogleLogin that knows the code 'good-code' (it is Ann) and no other;
    - `ann`: the GoogleUser that code gives.
    """

    def test_it_is_a_google_login(self, login):
        assert isinstance(login, GoogleLogin)

    def test_the_login_address_carries_the_state_and_where_to_come_back(self, login):
        query = parse_qs(urlparse(login.login_url('state-9', REDIRECT)).query)
        assert query['state'] == ['state-9']
        assert query['redirect_uri'] == [REDIRECT]

    def test_a_good_code_gives_the_person(self, login, ann):
        assert login.exchange('good-code', REDIRECT) == ann

    def test_a_wrong_code_fails_with_google_failed(self, login):
        with pytest.raises(GoogleFailed):
            login.exchange('wrong-code', REDIRECT)


class TestFakeGoogleLogin(GoogleLoginContract):
    @pytest.fixture
    def ann(self):
        return ANN

    @pytest.fixture
    def login(self, ann):
        fake = FakeGoogleLogin()
        fake.people['good-code'] = ann
        return fake


class TestHttpGoogleLogin(GoogleLoginContract):
    @pytest.fixture
    def ann(self):
        return ANN

    @pytest.fixture
    def login(self):
        settings = cloud_config().google

        def pretend_google(request: httpx.Request) -> httpx.Response:
            if str(request.url) == settings.token_url:
                good = parse_qs(request.content.decode()).get('code') == ['good-code']
                return httpx.Response(200, json={'access_token': 'at'}) if good else httpx.Response(400, json={})
            return httpx.Response(200, json={'sub': '1001', 'email': 'ann@example.com', 'name': 'Ann',
                                             'email_verified': True})

        return HttpGoogleLogin(settings, 'id', SecretStr('secret'), transport=httpx.MockTransport(pretend_google))
