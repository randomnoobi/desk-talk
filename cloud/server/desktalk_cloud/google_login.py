"""Log in with Google: the address to send a person to, and trading Google's code for who they are.

A failure says only what went wrong in plain words. It never holds the code, a token, the client secret or the text
of an httpx error (that text can hold an address with a secret in it), and it is raised `from None` so the httpx
error is not kept beside it either."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from desktalk_cloud.config import GoogleSettings
from desktalk_cloud.domain import GoogleUser
from desktalk_cloud.ports import GoogleFailed

__all__ = ['HttpGoogleLogin']


class HttpGoogleLogin:
    def __init__(self, settings: GoogleSettings, client_id: str, client_secret: SecretStr,
                 transport: httpx.BaseTransport | None = None):
        self._settings = settings
        self._client_id = client_id
        self._client_secret = client_secret         # a SecretStr: printing this object never shows it
        self._transport = transport

    def login_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({'client_id': self._client_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
                           'scope': self._settings.scopes, 'state': state, 'prompt': 'select_account'})
        return f'{self._settings.auth_url}?{query}'

    def exchange(self, code: str, redirect_uri: str) -> GoogleUser:
        try:
            with httpx.Client(transport=self._transport, timeout=self._settings.timeout_sec) as client:
                token = self._token(client, code, redirect_uri)
                return self._person(client, token)
        except GoogleFailed:
            raise
        except httpx.TimeoutException:
            raise GoogleFailed('Google did not answer in time') from None
        except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError):
            raise GoogleFailed('Could not reach Google') from None
        except (ValueError, KeyError, TypeError):
            raise GoogleFailed('Google gave an answer the server could not read') from None

    def _token(self, client: httpx.Client, code: str, redirect_uri: str) -> str:
        reply = client.post(self._settings.token_url, data={
            'code': code, 'client_id': self._client_id, 'client_secret': self._client_secret.get_secret_value(),
            'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'})
        body = _json_object(reply, 'Google would not accept the login code')
        token = body.get('access_token')
        if not isinstance(token, str) or not token:
            raise GoogleFailed('Google did not give a token')
        return token

    def _person(self, client: httpx.Client, token: str) -> GoogleUser:
        reply = client.get(self._settings.userinfo_url, headers={'Authorization': f'Bearer {token}'})
        body = _json_object(reply, 'Google would not say who logged in')
        sub, email, name = body.get('sub'), body.get('email'), body.get('name', body.get('email'))
        if not isinstance(sub, str) or not sub or not isinstance(email, str) or not isinstance(name, str):
            raise GoogleFailed('Google did not say who logged in')
        return GoogleUser(sub=sub, email=email, name=name or email,
                          email_verified=_is_yes(body.get('email_verified')))


def _json_object(reply: httpx.Response, refusal: str) -> dict[str, Any]:
    if reply.status_code != 200:
        raise GoogleFailed(f'{refusal} (Google answered {reply.status_code})')
    body = reply.json()
    if not isinstance(body, dict):
        raise GoogleFailed('Google gave an answer the server could not read')
    return body


def _is_yes(said: object) -> bool:
    """Google says true as a JSON true, and some accounts as the text "true". A 1 is not a yes."""
    return said is True or said == 'true'
