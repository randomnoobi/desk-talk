"""What the routes ask for: the parts, the Pi behind a token, the person behind a login cookie, and a request body
read with a size limit that does not trust Content-Length."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from desktalk_cloud.domain import Device, User, is_clean_text
from desktalk_cloud.tokens import hash_secret

if TYPE_CHECKING:
    from desktalk_cloud.app import CloudParts

SESSION_USER = 'user_id'                    # the login cookie holds this
BEARER = 'bearer '


def parts_of(request: Request) -> CloudParts:
    return request.app.state.parts


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get('authorization', '')
    if header.lower().startswith(BEARER) and header[len(BEARER):].strip():
        return header[len(BEARER):].strip()
    return None


def _no_token(request: Request) -> HTTPException:
    return HTTPException(401, parts_of(request).config.messages.bad_token)


def device_from_token(request: Request) -> Device:
    """The Pi that sent `Authorization: Bearer <token>`; 401 for a missing, wrong, cancelled or removed token."""
    parts = parts_of(request)
    token = _bearer_token(request)
    device = parts.store.device_for_token(hash_secret(token), parts.clock.now()) if token else None
    if device is None:
        raise _no_token(request)
    return device


def current_user(request: Request) -> User:
    """The person in the login cookie; 401 when nobody is logged in (or the person has been deleted)."""
    parts = parts_of(request)
    user_id = request.session.get(SESSION_USER)
    user = parts.store.get_user(user_id) if isinstance(user_id, str) else None
    if user is None:
        raise HTTPException(401, parts.config.messages.not_logged_in)
    return user


def owner_or_device(request: Request, device_id: str) -> Device:
    """The Pi `device_id`, for the Pi itself (its token) or for the person who owns it (the cookie).
    Anyone else gets 404, the same as for a Pi that doesn't exist. A token that is sent but wrong is a 401."""
    parts = parts_of(request)
    if request.headers.get('authorization') is not None:
        caller = device_from_token(request)
        found = caller if caller.id == device_id else None
    else:
        owner = current_user(request)
        found = parts.store.get_device(device_id) if is_clean_text(device_id) else None
        found = found if found is not None and found.user_id == owner.id else None
    if found is None:
        raise HTTPException(404, parts.config.messages.not_found)
    return found


async def read_json(request: Request, max_kb: int, parse_int: Callable[[str], Any] | None = None) -> Any:
    """The request body as JSON. Reads it in pieces and stops past `max_kb`, because Content-Length can be missing
    or wrong. 413 when too big, 400 when it isn't JSON. `parse_int` is json.loads's hook for whole numbers."""
    messages = parts_of(request).config.messages
    limit = max_kb * 1024
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, messages.body_too_big)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, messages.body_too_big)
        chunks.append(chunk)
    try:
        return json.loads(b''.join(chunks), parse_int=parse_int)
    except (ValueError, RecursionError) as error:
        raise HTTPException(400, messages.bad_body) from error
