"""
Asking the AI for an agent's line, and keeping the reply to that one agent.

The alert voice, the nudge voice and the chat answers all ask through this, so the reply sizes
(settings → llm.replies), the agent's own temperature and the one-speaker cleanup are the same everywhere.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.config import LlmSettings
from desktalk.core.chat.names import speaker_names
from desktalk.core.chat.replies import single_speaker_text
from desktalk.domain import AgentProfile, AIUnavailable
from desktalk.ports import ChatModel, Profiles

log = logging.getLogger(__name__)


class AgentAI:
    """`reply_max_chars`: the longest AI reply kept (settings → chat.reply_max_chars)."""

    def __init__(self, model: ChatModel, profiles: Profiles, llm: LlmSettings, reply_max_chars: int):
        self._model = model
        self._profiles = profiles
        self._llm = llm
        self._reply_max_chars = reply_max_chars
        self._still_asking: threading.Thread | None = None      # the last ask_briefly, which may have outlived its wait

    def call(self, messages: Sequence[Mapping[str, Any]], reply: str, agent: AgentProfile, background: bool) -> str:
        """The AI's reply; raises AIUnavailable. `reply` names the llm.replies size to use
        (no temperature there = the agent's own)."""
        size = getattr(self._llm.replies, reply)
        temperature = agent.temperature if size.temperature is None else size.temperature
        return self._model.chat(messages, max_tokens=size.max_tokens, temperature=temperature, background=background)

    def ask(self, messages: Sequence[Mapping[str, Any]], reply: str, agent: AgentProfile,
            background: bool) -> str | None:
        """The AI's reply, or None when it's unavailable."""
        try:
            return self.call(messages, reply, agent, background)
        except AIUnavailable as err:
            log.info('No AI %s line for %s: %s', reply, agent.id, err.reason)
            return None

    def ask_briefly(self, messages: Sequence[Mapping[str, Any]], reply: str, agent: AgentProfile) -> str | None:
        """Like ask, in the background, but waiting at most llm.reminder_wait_sec: the AI's turn, its tries and its
        answer all count, so an AI that has gone away can't hold this up. An answer that comes later is thrown away.
        While a late one is still going no other is started, so a gone AI can't pile up waiting threads."""
        if self._still_asking is not None and self._still_asking.is_alive():
            log.info('No AI %s line for %s: the AI is still busy with the last one', reply, agent.id)
            return None
        answer: list[str | None] = []

        def ask_it() -> None:
            try:
                answer.append(self.ask(messages, reply, agent, background=True))
            except Exception:           # noqa: BLE001  (a job on its own thread: nobody else would see this)
                log.exception('Asking the AI for a %s line failed', reply)

        self._still_asking = threading.Thread(target=ask_it, name='ai: ask briefly', daemon=True)
        self._still_asking.start()
        self._still_asking.join(self._llm.reminder_wait_sec)
        if not answer and self._still_asking.is_alive():
            log.info('No AI %s line for %s: no answer in %g s', reply, agent.id, self._llm.reminder_wait_sec)
        return answer[0] if answer else None

    def one_speaker(self, text: str, agent: AgentProfile) -> str:
        """The reply with lines the AI wrote for other agents taken out, cut to reply_max_chars."""
        return single_speaker_text(text, agent, speaker_names(self._profiles.all()), self._reply_max_chars)
