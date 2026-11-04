"""
The AI as everything but the chat you asked for sees it: reminders, warnings, small talk, memories, the daily recap,
lost-item alerts, anything that calls the AI by itself.

While llm.ai_url is the linked cloud server, this has no AI at all (ready is False and nothing is sent): you allowed
chat to pass through your cloud server, and nothing else. Those parts then use their ready-made words, or stay quiet.
It's worked out on every call, so linking while the app runs needs no restart. Ollama on your own computer isn't
affected.

    model = UnpromptedChatModel(asked_model, goes_to_cloud=lambda: ai.uses_cloud, reason=...)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from desktalk.domain import AIUnavailable
from desktalk.ports import ChatModel


class UnpromptedChatModel:
    """A ChatModel (desktalk/ports.py) around the real one. `reason`: what it says if something calls it anyway."""

    def __init__(self, inner: ChatModel, *, goes_to_cloud: Callable[[], bool], reason: str):
        self._inner = inner
        self._goes_to_cloud = goes_to_cloud
        self._reason = reason

    @property
    def label(self) -> str:
        return self._inner.label

    @property
    def ready(self) -> bool:
        return self._inner.ready and not self._goes_to_cloud()

    @property
    def is_local(self) -> bool:
        return self._inner.is_local

    @property
    def local_vision(self) -> bool:
        return self._inner.local_vision

    def problem(self) -> str | None:
        return self._inner.problem()

    def chat(self, messages: Sequence[Mapping[str, Any]], *, max_tokens: int, temperature: float,
             background: bool = False) -> str:
        self._refuse_if_cloud()
        return self._inner.chat(messages, max_tokens=max_tokens, temperature=temperature, background=background)

    def vision(self, jpeg: bytes, prompt: str, *, background: bool = True) -> str:
        self._refuse_if_cloud()
        return self._inner.vision(jpeg, prompt, background=background)

    def _refuse_if_cloud(self) -> None:
        if self._goes_to_cloud():
            raise AIUnavailable(self._reason)
