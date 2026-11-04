"""
The AI connection (chat + pictures), through the OpenAI-compatible API.

Which AI answers: llm.ai_url when it's set (how the Pi gets an AI, since it keeps no keys), otherwise .env decides: the
first of Gemini → OpenAI → Groq → Ollama that has a key (Ollama needs only OLLAMA_URL). When OLLAMA_URL is set,
questions about pictures go to Ollama's vision model even when the chat runs on a cloud AI, unless llm.send_pictures
is off. Model names and limits: config/settings.yaml → llm.

llm.ai_url gets text only, never a picture. It may be Ollama on the user's computer, or the linked cloud server. Only
when it matches the server of the saved link does a request carry the link's token, read for each request so linking
later needs no restart; any other address never sees it. The token is never logged. It counts as Ollama on the user's
own computer (kept loaded, its time limit, `uses_cloud` false so the characters' own words may go there) only when
llm.ai_on_my_computer says so and it isn't the linked server's address. Anything else may be the cloud server, so
only chat the user asked for goes there (UnpromptedChatModel).

Each request has a time limit (llm.timeout_sec, or llm.ollama_timeout_sec for Ollama), set with the
OpenAI client's own `timeout` option. Every failure is raised as AIUnavailable in plain words.
Trying again, pausing after a rate limit and the circuit breaker are ResilientChatModel's job, with one
exception: Ollama is asked again here after an empty reply (up to llm.max_retries times), and when it
still sends nothing the error is an AIRefused, so ResilientChatModel doesn't ask all over again.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from desktalk.adapters.llm.errors import AIRefused, error_from, is_rate_limit
from desktalk.config import LlmSettings, Secrets
from desktalk.config.prompts import Replies
from desktalk.domain import AIUnavailable
from desktalk.ports import Clock

log = logging.getLogger(__name__)

_NOT_FOR_PICTURES = ('multimodal', 'does not support')
_NO_KEY = 'ollama'                      # Ollama takes any key, and the client insists on one
_CLOUD_CHAT_PATH = '/v1'                # where a cloud server's chat lives (cloud/.../routers/chat_v1.py)
_USUAL_PORT = {'http': 80, 'https': 443}
_TOKEN_HIDDEN = '***'


def is_chat_address_of(ai_url: str, server: str) -> bool:
    """`ai_url` is the chat address of this cloud server: the same scheme, host and port, plus the chat's path."""
    try:
        url, home = urlsplit(ai_url), urlsplit(server)
        places = [(u.scheme, (u.hostname or '').lower(), u.port or _USUAL_PORT.get(u.scheme)) for u in (url, home)]
    except ValueError:
        return False
    return places[0] == places[1] and url.path.rstrip('/') == home.path.rstrip('/') + _CLOUD_CHAT_PATH


def openai_client(*, api_key: str, base_url: str | None = None) -> Any:
    """The real client. Its own retries are off: ResilientChatModel decides when to try again. So are redirects: one
    would carry the words, and the key, to a host nobody chose."""
    from openai import DefaultHttpxClient, OpenAI
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                  http_client=DefaultHttpxClient(follow_redirects=False))


class OpenAICompatibleChatModel:
    """The ChatModel (desktalk/ports.py) for Gemini, OpenAI, Groq and Ollama.

    client_factory(api_key=..., base_url=...) makes a client like openai.OpenAI; tests pass a fake.
    `replies`: config/prompts.yaml → replies (what it says when there is no AI, an empty reply or no picture).
    `cloud_link`: (the linked cloud server's address, the token) right now, or None when not linked."""

    def __init__(self, settings: LlmSettings, secrets: Secrets, clock: Clock,
                 client_factory: Callable[..., Any] | None = None, *, replies: Replies,
                 cloud_link: Callable[[], tuple[str, str] | None] = lambda: None):
        self._settings = settings
        self._clock = clock
        self._words = replies
        self._client: Any = None
        self._vision_client: Any = None
        self._model_name = ''
        self._label = replies.no_ai_label
        self._is_gemini = False
        self._local = False              # the chat runs on Ollama, on this computer (or the user's, llm.ai_url)
        self._ai_url = settings.ai_url.strip()
        self._cloud_link = cloud_link
        self._factory = client_factory or openai_client
        self._cloud_client: Any = None   # the client with the token in it, for that token
        self._cloud_client_token = ''
        self._problem: str | None = None
        self._vision_refused: str | None = None
        try:
            self._connect(secrets, self._factory)
        except ImportError:
            log.warning('The openai package is not installed, so the characters cannot chat.')
            self._client = self._vision_client = None
            self._label = self._words.no_ai_label

    def _connect(self, secrets: Secrets, factory: Callable[..., Any]) -> None:
        s = self._settings
        if self._ai_url:
            self._client = factory(api_key=_NO_KEY, base_url=self._ai_url)
            self._model_name, self._local = s.ollama_model, s.ai_on_my_computer
            self._label = f'Ollama {s.ollama_model}' if self._local else self._words.ai_url_label
            return
        ollama_url = secrets.ollama_url.strip()
        if ollama_url and s.send_pictures:
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
            self._model_name, self._label, self._local = s.ollama_model, f'Ollama {s.ollama_model}', True

    # ── what the app shows ──
    @property
    def label(self) -> str:
        return self._words.cloud_ai_label if self._token() else self._label

    @property
    def is_local(self) -> bool:
        """The chat runs on Ollama, on this computer or the user's (not the cloud server, once linked)."""
        return self._local and not self._token()

    @property
    def ready(self) -> bool:
        return self._client is not None

    @property
    def local_vision(self) -> bool:
        """Pictures go to Ollama on this computer, so looking at them costs no cloud quota."""
        return self._vision_client is not None

    @property
    def time_limit_sec(self) -> float:
        """The longest wait for one chat reply right now: Ollama's, or the cloud's once llm.ai_url is the linked
        server's address (read each time, so linking needs no restart)."""
        return self._settings.ollama_timeout_sec if self.is_local else self._settings.timeout_sec

    def problem(self) -> str | None:
        """The current AI problem in plain words, or None if the last call worked."""
        return self._problem

    @property
    def uses_cloud(self) -> bool:
        """llm.ai_url may be the cloud server right now: it's set and not marked as the user's own computer
        (llm.ai_on_my_computer), or it's the linked server's chat address."""
        return bool(self._ai_url) and (not self._local or bool(self._token()))

    def _token(self) -> str:
        """The token, when llm.ai_url is the linked cloud server's chat address right now; otherwise ''."""
        if not self._ai_url:
            return ''
        link = self._cloud_link()
        return link[1] if link and is_chat_address_of(self._ai_url, link[0]) else ''

    def _chat_client(self, token: str) -> Any:
        if not token:
            return self._client
        if token != self._cloud_client_token:           # the first time, or linked again
            self._cloud_client = self._factory(api_key=token, base_url=self._ai_url)
            self._cloud_client_token = token
        return self._cloud_client

    # ── calls ──
    def chat(self, messages: Sequence[Mapping[str, Any]], *, max_tokens: int, temperature: float,
             background: bool = False) -> str:
        if self._client is None:
            raise AIUnavailable(self._words.needs_an_ai)
        if self._ai_url and any(not isinstance(m.get('content'), str) for m in messages):
            raise AIRefused(self._words.pictures_stay_home)
        s = self._settings
        token = self._token()
        local = self._local and not token
        request: dict[str, Any] = {'model': self._model_name, 'max_tokens': max_tokens, 'temperature': temperature,
                                   'timeout': s.ollama_timeout_sec if local else s.timeout_sec}
        if local:
            effort = {'reasoning_effort': s.ollama_reasoning_effort} if s.ollama_reasoning_effort else {}
            request['extra_body'] = {'num_predict': max_tokens, **effort}
        elif self._is_gemini:
            request['extra_body'] = {'extra_body': {'google': {'thinking_config': {
                'thinking_budget': s.gemini_thinking_budget}}}}
        # Small local models sometimes answer with nothing: ask again, a little more creative each time.
        tries = s.max_retries if local else 1
        for attempt in range(tries):
            asked = [dict(m) for m in messages]
            if attempt:
                self._clock.sleep(s.ollama_retry_sec)
                request['temperature'] = min(temperature + s.ollama_warmer_per_retry * attempt, 1.0)
                if asked and asked[-1].get('role') == 'user':
                    asked[-1]['content'] = f'{asked[-1]["content"]}{self._words.ask_for_a_sentence}'
                log.info('%s sent back an empty reply, asking again (try %d)', self._label, attempt + 1)
            text = self._ask(self._chat_client(token), {**request, 'messages': asked}, token=token)
            if text:
                self._problem = None      # a call worked, so any earlier problem is over
                return text
        empty = self._words.empty_reply
        self._problem = empty
        if tries > 1:
            # Already asked again here: AIRefused so ResilientChatModel doesn't ask max_retries times more.
            raise AIRefused(empty)
        raise AIUnavailable(empty)

    def vision(self, jpeg: bytes, prompt: str, *, background: bool = True) -> str:
        client = self._vision_client or self._client
        if client is None:
            raise AIUnavailable(self._words.needs_an_ai)
        if self._ai_url or not self._settings.send_pictures:
            raise AIRefused(self._words.pictures_stay_home)
        if not jpeg:
            raise AIUnavailable(self._words.no_picture)
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

    def _ask(self, client: Any, request: dict[str, Any], picture_question: bool = False, token: str = '') -> str:
        """One request. Raises AIUnavailable (or AIRefused) in plain words. `token`: the one this client holds, kept
        out of what is logged or shown."""
        try:
            resp = client.chat.completions.create(**request)
        except Exception as exc:
            said = str(exc).replace(token, _TOKEN_HIDDEN) if token else str(exc)
            text = said.lower()
            if picture_question and not is_rate_limit(text) and any(w in text for w in _NOT_FOR_PICTURES):
                self._vision_refused = self._words.ai_errors.no_pictures.format(model=request['model'])
                log.warning('Questions about pictures are off: %s', self._vision_refused)
                raise AIRefused(self._vision_refused) from exc
            s = self._settings
            err = error_from(said, self._label, max_chars=s.error_text_chars, words=self._words.ai_errors)
            if token:
                if isinstance(err, AIRefused):      # the only key here is the link's, and the server said no to it
                    err = AIRefused(self._words.ai_errors.cloud_refused)
                log.warning('%s failed: %s', self._words.cloud_ai_label, said[:s.error_log_chars])
                self._problem = err.reason
                raise err from None                 # not `from exc`: what it said may hold the token
            log.warning('%s failed: %s', 'Picture question' if picture_question else self._label,
                        said[:s.error_log_chars])
            if not picture_question:
                self._problem = err.reason
            raise err from exc
        text = (resp.choices[0].message.content or '').strip()
        return text if picture_question else text.strip('"\'')

    # ── keeping Ollama's model loaded ──
    def ping(self) -> None:
        """Ask the chat AI something tiny. Failures are ignored: this only keeps Ollama's model in memory."""
        if self._client is None or not self.is_local:
            return
        try:
            self._client.chat.completions.create(model=self._model_name, messages=[{'role': 'user', 'content': 'hi'}],
                                                 max_tokens=1, timeout=self.time_limit_sec)
        except Exception as exc:
            log.debug('Ollama keep-alive failed: %s', exc)

    def keepalive_worker(self) -> OllamaKeepAlive | None:
        """A job that keeps Ollama's model loaded (None for a cloud AI). Add it to the Supervisor with
        start_after=llm.ollama_keepalive_start_sec."""
        return OllamaKeepAlive(self, self._settings.ollama_keepalive_sec) if self._local else None


class OllamaKeepAlive:
    """A PeriodicWorker: asks Ollama something tiny every few minutes, so the model stays loaded."""

    name = 'ollama keep-alive'

    def __init__(self, model: OpenAICompatibleChatModel, every_sec: float):
        self._model = model
        self._every = every_sec

    def tick(self, now: float) -> float:
        self._model.ping()
        return self._every
