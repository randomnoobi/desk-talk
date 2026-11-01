"""Logging in with Google (/auth/google/login and /callback) and logging out (/auth/logout).

The `state` is a random text kept in the signed login cookie and compared, in constant time, with the one Google
sends back; it works once. Nothing that Google or the person sends (code, state, token) goes to a log."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
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


def public_address(config: CloudConfig) -> str:
    """This server's address as a Pi and Google reach it, without a trailing slash."""
    return config.server.public_url.rstrip('/')


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
    return RedirectResponse(google.login_url(state, public_address(parts.config) + CALLBACK_PATH), status_code=302)


@router.get(CALLBACK_PATH)
def google_callback(request: Request, code: Annotated[str | None, Query()] = None,
                    state: Annotated[str | None, Query()] = None,
                    error: Annotated[str | None, Query()] = None) -> RedirectResponse:
    parts = parts_of(request)
    messages = parts.config.messages
    google = _google(request)
    expected = request.session.pop(SESSION_STATE, None)         # a state works once
    if (not isinstance(expected, str) or state is None
            or not secrets.compare_digest(state.encode('utf-8', 'replace'), expected.encode('utf-8'))):
        raise HTTPException(400, messages.bad_state)
    if error is not None or not code:
        raise HTTPException(400, messages.google_failed)        # the person said no at Google, or Google sent no code
    try:
        person = google.exchange(code, public_address(parts.config) + CALLBACK_PATH)
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


@router.post('/auth/logout')
def logout(request: Request) -> dict[str, bool]:
    request.session.clear()                                     # a person's login and an admin's, both
    return {'ok': True}
