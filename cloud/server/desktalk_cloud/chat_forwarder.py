"""Sends a Pi's chat request to the AI company with this server's key, and brings the reply back.

The chat text can hold to-dos and what the user typed. It passes through here and is never kept: only the status and
the time taken are logged, and a failure is raised in fixed plain words. An error from the AI company (its reply or
the text of an httpx error) can hold the chat text, so none of it is logged or passed on."""

from __future__ import annotations

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
        started = self._clock.monotonic()
        try:
            reply = self._client.post(s.url, json=request, headers={'authorization': f'Bearer {self._api_key}'},
                                      timeout=s.timeout_sec, follow_redirects=False)
        except Exception as error:                  # its message can hold the chat text: only the kind is logged
            log.warning('The chat AI did not answer (%s) after %.1f s', type(error).__name__,
                        self._clock.monotonic() - started)
            raise ChatFailed('The AI company did not answer.') from None
        took = self._clock.monotonic() - started
        log.info('The chat AI answered with status %d in %.1f s', reply.status_code, took)
        if reply.status_code not in OK_STATUSES:
            raise ChatFailed('The AI company refused the request.')
        try:
            body = reply.json()
        except ValueError:
            raise ChatFailed('The AI company sent back something that is not JSON.') from None
        choices = body.get('choices') if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ChatFailed('The AI company sent back no answer.')
        return body
