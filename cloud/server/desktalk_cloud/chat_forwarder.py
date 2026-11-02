"""Sends a Pi's chat request to the AI company with this server's key, and brings the reply back.

The chat text can hold to-dos and what the user typed. It passes through here and is never kept: only the status and
the time taken are logged, and a failure is raised in fixed plain words. An error from the AI company (its reply or
the text of an httpx error) can hold the chat text, so none of it is logged or passed on.

It runs on the event loop, inside one `asyncio.timeout(chat.deadline_sec)`, which stops it wherever it has got to:
connecting, waiting for the headers, or reading a reply sent a byte at a time. httpx's own timeout is for one read, so
it couldn't do this, and it is turned off."""

from __future__ import annotations

import asyncio
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

    def __init__(self, settings: ChatSettings, api_key: str, clock: Clock, *, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._api_key = api_key
        self._clock = clock
        self._client = client or httpx.AsyncClient()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: Sequence[Mapping[str, Any]], max_tokens: int | None,
                       temperature: float | None) -> dict[str, Any]:
        s = self._settings
        request: dict[str, Any] = {'model': s.model, 'messages': list(messages), 'stream': False}
        if max_tokens is not None:
            request['max_tokens'] = max_tokens
        if temperature is not None:
            request['temperature'] = temperature
        # Asks for it unsqueezed, so the event loop spends no time unsqueezing a reply.
        headers = {'authorization': f'Bearer {self._api_key}', 'accept-encoding': 'identity'}
        started = self._clock.monotonic()
        try:
            async with asyncio.timeout(s.deadline_sec):
                async with self._client.stream('POST', s.url, json=request, headers=headers, timeout=None,
                                               follow_redirects=False) as reply:  # leaving it closes it, on every path
                    status = reply.status_code
                    content = await self._read(reply) if status in OK_STATUSES else b''
        except ChatFailed:
            raise
        except TimeoutError:
            log.warning('The chat AI went past the deadline after %.1f s', self._clock.monotonic() - started)
            raise ChatFailed('The AI company took too long.') from None
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

    async def _read(self, reply: httpx.Response) -> bytes:
        """The whole reply, a piece at a time. Raises ChatFailed as soon as it is too big."""
        pieces: list[bytes] = []
        size = 0
        async for piece in reply.aiter_bytes():
            size += len(piece)
            if size > self._settings.max_body_kb * 1024:
                log.warning('The chat AI sent too long a reply')
                raise ChatFailed('The AI company sent back too much.')
            pieces.append(piece)
        return b''.join(pieces)
