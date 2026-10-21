"""
Asking the AI from a background job.

Nobody is waiting on these jobs, so when there's no AI, or it can't answer right now
(AIUnavailable: no key, out of quota, down, too slow), the job just says nothing this round
(or uses its built-in line). These helpers turn that into None instead of an error.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.config import AiReply
from desktalk.domain import AIUnavailable
from desktalk.ports import ChatModel

log = logging.getLogger(__name__)


def ask(chat: ChatModel, messages: Sequence[Mapping[str, Any]], reply: AiReply, *, own_temperature: float,
        background: bool, what: str) -> str | None:
    """The AI's reply, or None. `reply` is the size and temperature setting (settings → llm.replies);
    `own_temperature` is used when that setting has no temperature (the speaking agent's own).
    `what` names the job's question for the log."""
    if not chat.ready:
        return None
    temperature = reply.temperature if reply.temperature is not None else own_temperature
    try:
        return chat.chat(messages, max_tokens=reply.max_tokens, temperature=temperature, background=background)
    except AIUnavailable as exc:
        log.info('No AI answer for %s this time: %s', what, exc.reason)
        return None


def look(vision: ChatModel | None, jpeg: bytes | None, prompt: str, *, background: bool, what: str) -> str | None:
    """The AI's answer about a picture, or None (no vision AI set up, no picture, or it can't answer)."""
    if vision is None or not jpeg:
        return None
    try:
        return vision.vision(jpeg, prompt, background=background)
    except AIUnavailable as exc:
        log.info('No AI answer for %s this time: %s', what, exc.reason)
        return None
