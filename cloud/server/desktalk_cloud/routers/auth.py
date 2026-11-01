"""Logging in with Google (/auth/google/login and /callback) and logging out (/auth/logout).

The `state` is a random text kept in the signed login cookie and compared, in constant time, with the one Google
sends back. It is used up when the person comes back, but that only holds for one browser: what really stops a
replay is Google's own code, which works once. Nothing that Google or the person sends (code, state, token) goes
to a log.

Google sends the person back to `server.login_url`'s address (the one the login cookie belongs to, which Google must
accept: `localhost` or a real domain), not to `server.public_url`, which is the address a Pi reaches and may be a
home network number."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from desktalk_cloud.config import CloudConfig
from desktalk_cloud.deps import SESSION_USER, parts_of
from desktalk_cloud.domain import is_clean_text
from desktalk_cloud.ports import GoogleFailed, GoogleLogin

router = APIRouter()
log = logging.getLogger(__name__)

SESSION_STATE = 'google_state'
STATE_BYTES = 32                    # 256 bits of randomness, like a token
CALLBACK_PATH = '/auth/google/callback'


JSON = 'application/json'


def public_address(config: CloudConfig) -> str:
    """This server's address as a Pi reaches it (shown in the setup command), without a trailing slash."""
    return config.server.public_url.rstrip('/')


def google_return_address(config: CloudConfig) -> str:
    """Where Google sends the person back to: the address of `server.login_url` (just scheme and host, no page), or
    `public_url` when that is empty."""
    server = config.server
    if not server.login_url:
        return public_address(config)
    parts = urlsplit(server.login_url)
    return f'{parts.scheme}://{parts.netloc}' if parts.scheme and parts.netloc else server.login_url.rstrip('/')


def needs_json(request: Request) -> None:
    """For a route that changes things and reads its cookie: refuse anything not sent as JSON (415). A web page
    on another address can send a plain form or text with the cookie, but not as application/json without the
    server's say-so, and `same_site=lax` does not cover other services on the same home address."""
    kind = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if kind != JSON:
        raise HTTPException(415, parts_of(request).config.prompts['site']['needs_json'])


def _google(request: Request) -> GoogleLogin:
    parts = parts_of(request)
    if parts.google is None:
        raise HTTPException(503, parts.config.messages.google_not_set_up)
    return parts.google


@router.get('/auth/google/login')
def google_login(request: Request) -> RedirectResponse:
    parts = parts_of(request)
    google = _google(request)
    state = secrets.token_urlsafe(STATE_BYTES)
    request.session[SESSION_STATE] = state
    back_to = google_return_address(parts.config) + CALLBACK_PATH
    return RedirectResponse(google.login_url(state, back_to), status_code=302)


@router.get(CALLBACK_PATH)
def google_callback(request: Request, code: Annotated[str | None, Query()] = None,
                    state: Annotated[str | None, Query()] = None,
                    error: Annotated[str | None, Query()] = None) -> RedirectResponse:
    parts = parts_of(request)
    messages = parts.config.messages
    google = _google(request)
    expected = request.session.pop(SESSION_STATE, None)         # used up here (for this browser)
    if (not isinstance(expected, str) or state is None
            or not secrets.compare_digest(state.encode('utf-8', 'replace'), expected.encode('utf-8'))):
        raise HTTPException(400, messages.bad_state)
    if error is not None or not code:
        raise HTTPException(400, messages.google_failed)        # the person said no at Google, or Google sent no code
    try:
        person = google.exchange(code, google_return_address(parts.config) + CALLBACK_PATH)
    except GoogleFailed as failure:
        log.warning('Google login failed: %s', failure)
        raise HTTPException(502, messages.google_failed) from None
    if not person.email_verified:
        raise HTTPException(403, messages.email_not_verified)
    if not all(is_clean_text(text) for text in (person.sub, person.email, person.name)):
        log.warning('Google login refused: a name or address has a character the server can not keep')
        raise HTTPException(502, messages.google_failed)
    user = parts.store.upsert_user(person.sub, person.email, person.name, parts.clock.now())
    request.session[SESSION_USER] = user.id
    log.info('Person %s logged in', user.id)
    return RedirectResponse(parts.config.server.login_url or '/', status_code=303)


@router.post('/auth/logout', dependencies=[Depends(needs_json)])
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()                                     # a person's login and an admin's, both
    return {'ok': True}
