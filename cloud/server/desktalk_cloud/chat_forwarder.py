"""Sends a Pi's chat request to the AI company with this server's key, and brings the reply back.

The chat text can hold to-dos and what the user typed. It passes through here and is never kept: only the status and
the time taken are logged, and a failure is raised in fixed plain words. An error from the AI company (its reply or
the text of an httpx error) can hold the chat text, so none of it is logged or passed on.

httpx's timeout is for one read, so an AI company sending a byte at a time could hold a chat (and its thread) open for
ever. The reply is read in pieces and each piece checks `chat.deadline_sec` and the size limit. The wait for the next
piece is at most `chat.timeout_sec`, so a chat ends by deadline_sec + timeout_sec. What this can't see is the reply's
headers arriving a byte at a time: they come before the first piece. They are limited in size by the HTTP library, not
in time."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from desktalk_cloud.config import ChatSettings
from desktalk_cloud.ports import ChatFailed, Clock

log = logging.getLogger(__name__)
OK_STATUSES = range(200, 300)


class HttpChatForwarder:
    """The ChatForwarder (ports.py) for any OpenAI-compatible chat completions address (`chat.url`).
    `client` is for tests (an httpx client with a fake transport)."""

    def __init__(self, settings: ChatSettings, api_key: str, clock: Clock, *, client: httpx.Client | None = None):
        self._settings = settings
        self._api_key = api_key
        self._clock = clock
        self._client = client or httpx.Client()

    def complete(self, messages: Sequence[Mapping[str, Any]], max_tokens: int | None,
                 temperature: float | None) -> dict[str, Any]:
        s = self._settings
        request: dict[str, Any] = {'model': s.model, 'messages': list(messages), 'stream': False}
        if max_tokens is not None:
            request['max_tokens'] = max_tokens
        if temperature is not None:
            request['temperature'] = temperature
        # Asks for it unsqueezed: a gzip reply can arrive for a long time before any piece comes out to be checked.
        headers = {'authorization': f'Bearer {self._api_key}', 'accept-encoding': 'identity'}
        started = self._clock.monotonic()
        try:
            with self._client.stream('POST', s.url, json=request, headers=headers, timeout=s.timeout_sec,
                                     follow_redirects=False) as reply:          # leaving this closes it, on every path
                status = reply.status_code
                content = self._read(reply, started) if status in OK_STATUSES else b''
        except ChatFailed:
            raise
        except Exception as error:                  # its message can hold the chat text: only the kind is logged
            log.warning('The chat AI did not answer (%s) after %.1f s', type(error).__name__,
                        self._clock.monotonic() - started)
            raise ChatFailed('The AI company did not answer.') from None
        took = self._clock.monotonic() - started
        log.info('The chat AI answered with status %d in %.1f s', status, took)
        if status not in OK_STATUSES:
            raise ChatFailed('The AI company refused the request.')
        try:
            body = json.loads(content)
        except ValueError:
            raise ChatFailed('The AI company sent back something that is not JSON.') from None
        choices = body.get('choices') if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ChatFailed('The AI company sent back no answer.')
        return body

    def _read(self, reply: httpx.Response, started: float) -> bytes:
        """The whole reply, a piece at a time. Raises ChatFailed as soon as it is too late or too big."""
        s = self._settings
        pieces: list[bytes] = []
        size = 0
        for piece in reply.iter_bytes():
            took = self._clock.monotonic() - started
            if took > s.deadline_sec:
                log.warning('The chat AI went past the deadline after %.1f s', took)
                raise ChatFailed('The AI company took too long.')
            size += len(piece)
            if size > s.max_body_kb * 1024:
                log.warning('The chat AI sent too long a reply after %.1f s', took)
                raise ChatFailed('The AI company sent back too much.')
            pieces.append(piece)
        return b''.join(pieces)
