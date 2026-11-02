"""POST /v1/chat/completions: a Pi chats through this server's AI key. Text only.

The chat text can hold to-dos and what the user typed. It is sent on to the AI company and is never stored or logged
here, and nothing from a failure (its reply, an error's text) is logged or sent back."""

from __future__ import annotations

import logging
from collections import deque
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from desktalk_cloud.deps import device_from_token, parts_of, read_json
from desktalk_cloud.domain import Device
from desktalk_cloud.ports import ChatFailed

router = APIRouter()
log = logging.getLogger(__name__)

ROLES = frozenset({'system', 'user', 'assistant'})         # the chat roles of the OpenAI format
IMAGE_KEYS = frozenset({'image_url', 'image', 'images', 'input_image'})
DATA_IMAGE = 'data:image/'
TEMPERATURE_MAX = 2                                        # the top of the OpenAI temperature range (0 is the bottom)


class _Refused(Exception):
    """A request this route will not pass on. `images` is True when the reason is a picture."""

    def __init__(self, images: bool = False):
        super().__init__()
        self.images = images


def _has_image(body: Any) -> bool:
    """Whether a picture is anywhere in the request, at any depth. Walks with a list, not recursion, so a deeply
    nested body can't crash it."""
    todo: deque[Any] = deque([body])
    while todo:
        item = todo.pop()
        if isinstance(item, str):
            if DATA_IMAGE in item.lower():
                return True
        elif isinstance(item, dict):
            for key, value in item.items():
                if str(key).lower() in IMAGE_KEYS or DATA_IMAGE in str(key).lower():
                    return True
                todo.append(value)
        elif isinstance(item, list):
            todo.extend(item)
    return False


def _is_text(value: Any) -> bool:
    """A string that can be sent on as UTF-8 (a lone surrogate such as "\\ud800" can't)."""
    if not isinstance(value, str):
        return False
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def _clean_content(content: Any) -> str | list[dict[str, str]]:
    """The content of one message: text, or a non-empty list of text parts. Raises _Refused for anything else."""
    if isinstance(content, str):
        if not _is_text(content):
            raise _Refused()
        return content
    if not isinstance(content, list) or not content:
        raise _Refused()
    parts: list[dict[str, str]] = []
    for part in content:
        if not isinstance(part, dict):
            raise _Refused()
        kind = part.get('type')
        if not isinstance(kind, str) or kind != 'text':
            raise _Refused(images=True)                   # a picture, audio or anything that is not text
        if not _is_text(part.get('text')):
            raise _Refused()
        parts.append({'type': 'text', 'text': part['text']})
    return parts


def _clean_messages(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise _Refused()
    clean: list[dict[str, Any]] = []
    for message in raw:
        role = message.get('role') if isinstance(message, dict) else None
        if not isinstance(role, str) or role not in ROLES:      # a list or dict can't be looked up in a set
            raise _Refused()
        clean.append({'role': role, 'content': _clean_content(message.get('content'))})
    return clean


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean_options(body: dict[str, Any], max_tokens_cap: int) -> tuple[int, float | None]:
    """The reply length (lowered to `max_tokens_cap`, which is also used when none is asked for) and the temperature."""
    max_tokens, temperature = body.get('max_tokens'), body.get('temperature')
    if max_tokens is not None and (not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1):
        raise _Refused()
    # The range is checked on its own: a whole number of hundreds of digits is too big for float maths (isfinite).
    # A NaN fails the range too, and an infinity is beyond it.
    if temperature is not None and (not _is_number(temperature) or not 0 <= temperature <= TEMPERATURE_MAX):
        raise _Refused()
    return min(max_tokens or max_tokens_cap, max_tokens_cap), temperature


@router.post('/v1/chat/completions')
async def chat_completions(request: Request, device: Annotated[Device, Depends(device_from_token)]) -> Any:
    parts = parts_of(request)
    config = parts.config
    messages = config.messages
    if parts.chat_forwarder is None:
        raise HTTPException(503, messages.chat_no_key)
    if not parts.chat_limiter.hit(device.id):
        raise HTTPException(429, messages.chat_too_many)
    body = await read_json(request, config.chat.max_body_kb)         # 413 or 400, before anything is looked at
    try:
        if _has_image(body):
            raise _Refused(images=True)
        if not isinstance(body, dict):
            raise _Refused()
        clean = _clean_messages(body.get('messages'))
        max_tokens, temperature = _clean_options(body, config.chat.max_tokens_cap)
    except _Refused as refused:
        raise HTTPException(400, messages.chat_no_images if refused.images else messages.bad_body) from None
    if not parts.chat_slots.acquire(blocking=False):             # refused at once: waiting would hold the request open
        raise HTTPException(503, messages.chat_busy)
    try:
        return await parts.chat_forwarder.complete(clean, max_tokens, temperature)
    except ChatFailed:
        pass
    except Exception as error:                          # its text can hold the chat text: only the kind is logged
        log.warning('The chat forwarder broke (%s)', type(error).__name__)
    finally:                                            # on a cancel too, which the two lines above let through
        parts.chat_slots.release()
    raise HTTPException(502, messages.chat_failed)
