"""
The AI connection (chat + pictures), through the OpenAI-compatible API.

Which AI answers is decided by .env: the first of Gemini → OpenAI → Groq → Ollama that has a key
(Ollama needs only OLLAMA_URL). When OLLAMA_URL is set, questions about pictures go to Ollama's
vision model even when the chat runs on a cloud AI. Model names and limits: config/settings.yaml → llm.

Each request has a time limit (llm.timeout_sec, or llm.ollama_timeout_sec for Ollama), set with the
OpenAI client's own `timeout` option. Every failure is raised as AIUnavailable in plain words.
Trying again, pausing after a rate limit and the circuit breaker are ResilientChatModel's job.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from desktalk.adapters.llm.errors import AIRefused, error_from, is_rate_limit
from desktalk.config import LlmSettings, Secrets
from desktalk.domain import AIUnavailable
from desktalk.ports import Clock

log = logging.getLogger(__name__)

NO_AI_LABEL = 'No AI (add a key to .env)'
NO_AI_REASON = ('The characters need an AI to chat. Add a key to .env (GEMINI_API_KEY is free; '
                'or set OLLAMA_URL) and restart the server.')
EMPTY_REPLY_REASON = 'The AI sent back an empty reply. Try saying it again.'
NO_PICTURE_REASON = 'There is no picture to look at yet.'
ASK_FOR_A_SENTENCE = '\n(Please respond with at least one sentence.)'
_NOT_FOR_PICTURES = ('multimodal', 'does not support')


def openai_client(*, api_key: str, base_url: str | None = None) -> Any:
    """The real client. Its own retries are off: ResilientChatModel decides when to try again."""
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=0)


class OpenAICompatibleChatModel:
    """The ChatModel (desktalk/ports.py) for Gemini, OpenAI, Groq and Ollama.

    client_factory(api_key=..., base_url=...) makes a client like openai.OpenAI; tests pass a fake."""

    def __init__(self, settings: LlmSettings, secrets: Secrets, clock: Clock,
                 client_factory: Callable[..., Any] | None = None):
        self._settings = settings
        self._clock = clock
        self._client: Any = None
        self._vision_client: Any = None
        self._model_name = ''
        self._label = NO_AI_LABEL
        self._is_gemini = False
        self.is_local = False            # the chat runs on Ollama, on this computer
        self._problem: str | None = None
        self._vision_refused: str | None = None
        try:
            self._connect(secrets, client_factory or openai_client)
        except ImportError:
            log.warning('The openai package is not installed, so the characters cannot chat.')
            self._client = self._vision_client = None
            self._label = NO_AI_LABEL

    def _connect(self, secrets: Secrets, factory: Callable[..., Any]) -> None:
        s = self._settings
        ollama_url = secrets.ollama_url.strip()
        if ollama_url:
            # A separate client for pictures, so the chat can run on Gemini while Ollama looks at pictures.
            self._vision_client = factory(api_key='ollama', base_url=ollama_url)
        gemini, openai, groq = (k.get_secret_value().strip() for k in
                                (secrets.gemini_api_key, secrets.openai_api_key, secrets.groq_api_key))
        if gemini:
            self._client = factory(api_key=gemini, base_url=s.gemini_url)
            self._model_name, self._label, self._is_gemini = s.gemini_model, f'Gemini {s.gemini_model}', True
        elif openai:
            self._client = factory(api_key=openai, base_url=None)
            self._model_name, self._label = s.openai_model, f'OpenAI {s.openai_model}'
        elif groq:
            self._client = factory(api_key=groq, base_url=s.groq_url)
            self._model_name, self._label = s.groq_model, f'Groq {s.groq_model}'
        elif ollama_url:
            self._client = factory(api_key='ollama', base_url=ollama_url)
            self._model_name, self._label, self.is_local = s.ollama_model, f'Ollama {s.ollama_model}', True

    # ── what the app shows ──
    @property
    def label(self) -> str:
        return self._label

    @property
    def ready(self) -> bool:
        return self._client is not None

    @property
    def local_vision(self) -> bool:
        """Pictures go to Ollama on this computer, so looking at them costs no cloud quota."""
        return self._vision_client is not None

    @property
    def time_limit_sec(self) -> float:
        """The longest wait for one chat reply."""
        return self._settings.ollama_timeout_sec if self.is_local else self._settings.timeout_sec

    def problem(self) -> str | None:
        """The current AI problem in plain words, or None if the last call worked."""
        return self._problem

    # ── calls ──
    def chat(self, messages: Sequence[Mapping[str, Any]], *, max_tokens: int, temperature: float,
             background: bool = False) -> str:
        if self._client is None:
            raise AIUnavailable(NO_AI_REASON)
        s = self._settings
        request: dict[str, Any] = {'model': self._model_name, 'max_tokens': max_tokens,
                                   'temperature': temperature, 'timeout': self.time_limit_sec}
        if self.is_local:
            request['extra_body'] = {'num_predict': max_tokens}
        elif self._is_gemini:
            request['extra_body'] = {'extra_body': {'google': {'thinking_config': {
                'thinking_budget': s.gemini_thinking_budget}}}}
        # Small local models sometimes answer with nothing: ask again, a little more creative each time.
        tries = s.max_retries if self.is_local else 1
        for attempt in range(tries):
            asked = [dict(m) for m in messages]
            if attempt:
                self._clock.sleep(s.ollama_retry_sec)
                request['temperature'] = min(temperature + s.ollama_warmer_per_retry * attempt, 1.0)
                if asked and asked[-1].get('role') == 'user':
                    asked[-1]['content'] = f'{asked[-1]["content"]}{ASK_FOR_A_SENTENCE}'
                log.info('%s sent back an empty reply, asking again (try %d)', self._label, attempt + 1)
            text = self._ask(self._client, {**request, 'messages': asked})
            if text:
                self._problem = None      # a call worked, so any earlier problem is over
                return text
        self._problem = EMPTY_REPLY_REASON
        raise AIUnavailable(EMPTY_REPLY_REASON)

    def vision(self, jpeg: bytes, prompt: str, *, background: bool = True) -> str:
        client = self._vision_client or self._client
        if client is None:
            raise AIUnavailable(NO_AI_REASON)
        if not jpeg:
            raise AIUnavailable(NO_PICTURE_REASON)
        if self._vision_refused:
            raise AIRefused(self._vision_refused)
        s = self._settings
        # A vision model name like "llava" only exists on Ollama; on a cloud AI use its own model.
        model = s.vision_model if self.local_vision else self._model_name
        picture = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
        request: dict[str, Any] = {
            'model': model, 'max_tokens': s.replies.vision.max_tokens,
            'timeout': s.ollama_timeout_sec if self.local_vision else s.timeout_sec,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': picture}},
            ]}]}
        if s.replies.vision.temperature is not None:
            request['temperature'] = s.replies.vision.temperature
        try:
            return self._ask(client, request, picture_question=True)
        except AIRefused:
            raise
        except AIUnavailable as err:
            if err.rate_limited:
                self._problem = err.reason     # pictures on a cloud AI use the same quota as the chat
            raise

    def _ask(self, client: Any, request: dict[str, Any], picture_question: bool = False) -> str:
        """One request. Raises AIUnavailable (or AIRefused) in plain words."""
        try:
            resp = client.chat.completions.create(**request)
        except Exception as exc:
            text = str(exc).lower()
            if picture_question and not is_rate_limit(text) and any(w in text for w in _NOT_FOR_PICTURES):
                self._vision_refused = (f'The model "{request["model"]}" cannot look at pictures. Set '
                                        f'llm.vision_model in config/settings.yaml to a vision model '
                                        f'(for example `ollama pull llava`).')
                log.warning('Questions about pictures are off: %s', self._vision_refused)
                raise AIRefused(self._vision_refused) from exc
            err = error_from(exc, self._label)
            log.warning('%s failed: %s', 'Picture question' if picture_question else self._label, str(exc)[:200])
            if not picture_question:
                self._problem = err.reason
            raise err from exc
        text = (resp.choices[0].message.content or '').strip()
        return text if picture_question else text.strip('"\'')

    # ── keeping Ollama's model loaded ──
    def ping(self) -> None:
        """Ask the chat AI something tiny. Failures are ignored: this only keeps Ollama's model in memory."""
        if self._client is None:
            return
        try:
            self._client.chat.completions.create(model=self._model_name, messages=[{'role': 'user', 'content': 'hi'}],
                                                 max_tokens=1, timeout=self.time_limit_sec)
        except Exception as exc:
            log.debug('Ollama keep-alive failed: %s', exc)

    def keepalive_worker(self) -> OllamaKeepAlive | None:
        """A job that keeps Ollama's model loaded (None for a cloud AI). Add it to the Supervisor with
        start_after=llm.ollama_keepalive_start_sec."""
        return OllamaKeepAlive(self, self._settings.ollama_keepalive_sec) if self.is_local else None


class OllamaKeepAlive:
    """A PeriodicWorker: asks Ollama something tiny every few minutes, so the model stays loaded."""

    name = 'ollama keep-alive'

    def __init__(self, model: OpenAICompatibleChatModel, every_sec: float):
        self._model = model
        self._every = every_sec

    def tick(self, now: float) -> float:
        self._model.ping()
        return self._every
