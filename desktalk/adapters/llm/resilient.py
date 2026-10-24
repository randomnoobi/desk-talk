"""
ResilientChatModel: wraps any ChatModel so a slow, broken or out-of-quota AI never stalls Desk Talk.

    model = ResilientChatModel(OpenAICompatibleChatModel(...), clock, config.llm, time_limit_sec=ai.time_limit_sec)

What it adds (numbers: config/settings.yaml → llm):
- Calls are spaced at least min_interval_sec apart (protects free limits).
- A failed call is tried again after retry_start_sec, doubling up to retry_max_sec, max_retries tries in all.
  A refusal (bad key, unknown model) is not tried again. A failed picture question waits
  vision_retry_sec × the try number instead.
- Time limit: no new try starts once a call has been going for time_limit_sec. Picture questions have
  their own limit: ollama_timeout_sec when they go to Ollama on this computer, else timeout_sec. (Each
  single request is cut off at its own limit by the OpenAI client's `timeout` option.)
- Rate limits: when you're waiting on a reply, one more try after rate_limit_retry_sec. Background calls
  never try again, and for pause_on_limit_sec after a rate limit they aren't sent at all, so the quota
  is saved for replying to you. (Picture questions on Ollama, on this computer, carry on.)
- Circuit breaker: after breaker_failures failed calls in a row the AI is left alone for breaker_open_sec;
  then one call checks whether it's back. Chat and pictures have a breaker each, because pictures often
  go to a different AI (Ollama).

All timing uses the Clock, so tests move time by hand.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from desktalk.adapters.llm.errors import AIRefused
from desktalk.config import LlmSettings
from desktalk.domain import AIUnavailable
from desktalk.ports import ChatModel, Clock

log = logging.getLogger(__name__)

CHAT, VISION = 'chat', 'vision'


def _plain_errors(ask: Callable[[], str], max_chars: int) -> str:
    """Run one try. A ChatModel should only raise AIUnavailable; anything else is turned into one here, its text
    cut to `max_chars` letters (settings → llm.error_text_chars)."""
    try:
        return ask()
    except AIUnavailable:
        raise
    except Exception as exc:
        log.exception('The AI connection raised an unexpected error')
        raise AIUnavailable(f'The AI returned an error: {str(exc)[:max_chars]}') from exc


class _Breaker:
    """Counts failed calls in a row; when there are too many, says no for a while, then lets one call check."""

    def __init__(self, failures_to_open: int, open_sec: float):
        self.failures_to_open = failures_to_open
        self.open_sec = open_sec
        self.failures = 0
        self.open_until: float | None = None
        self.checking = False

    def is_open(self, now: float) -> bool:
        return self.open_until is not None and (now < self.open_until or self.checking)

    def let_in(self, now: float) -> bool:
        """True if a call may go ahead now; the first call after the pause becomes the check."""
        if self.open_until is None:
            return True
        if now < self.open_until or self.checking:
            return False
        self.checking = True
        return True

    def worked(self) -> None:
        self.failures, self.open_until, self.checking = 0, None, False

    def failed(self, now: float) -> bool:
        """Count a failed call. True if the breaker just opened."""
        self.failures += 1
        was_checking, self.checking = self.checking, False
        if was_checking or self.failures >= self.failures_to_open:
            self.open_until = now + self.open_sec
            return True
        return False

    def seconds_left(self, now: float) -> int:
        return max(1, round((self.open_until or now) - now))


class ResilientChatModel:
    """A ChatModel (desktalk/ports.py) around another ChatModel."""

    def __init__(self, inner: ChatModel, clock: Clock, settings: LlmSettings, *, time_limit_sec: float | None = None):
        self._inner = inner
        self._clock = clock
        self._s = settings
        self._time_limit = {
            CHAT: settings.timeout_sec if time_limit_sec is None else time_limit_sec,
            VISION: settings.ollama_timeout_sec if self.local_vision else settings.timeout_sec,
        }
        self._lock = threading.Lock()
        self._breakers = {kind: _Breaker(settings.breaker_failures, settings.breaker_open_sec)
                          for kind in (CHAT, VISION)}
        self._problem: str | None = None
        self._resting_until: float | None = None     # background calls rest until then (after a rate limit)
        self._next_turn = float('-inf')              # the earliest the next call may start

    # ── what the app shows ──
    @property
    def label(self) -> str:
        return self._inner.label

    @property
    def ready(self) -> bool:
        return self._inner.ready

    @property
    def is_local(self) -> bool:
        """The chat runs on this computer (Ollama). Not every ChatModel says; those that don't aren't."""
        return bool(getattr(self._inner, 'is_local', False))

    @property
    def local_vision(self) -> bool:
        """Pictures go to Ollama on this computer, so they cost no cloud quota."""
        return bool(getattr(self._inner, 'local_vision', False))

    def problem(self) -> str | None:
        """The current AI problem in plain words, or None if the last call worked."""
        with self._lock:
            return self._problem_now(self._clock.monotonic())

    def _problem_now(self, now: float) -> str | None:
        breaker = self._breakers[CHAT]
        if self._problem and breaker.is_open(now):
            return (f'{self._problem} (It failed {breaker.failures} times in a row, so Desk Talk is leaving it '
                    f'alone for {breaker.seconds_left(now)} seconds.)')
        return self._problem

    # ── calls ──
    def chat(self, messages: Sequence[Mapping[str, Any]], *, max_tokens: int, temperature: float,
             background: bool = False) -> str:
        return self._call(CHAT, background, lambda: self._inner.chat(
            messages, max_tokens=max_tokens, temperature=temperature, background=background))

    def vision(self, jpeg: bytes, prompt: str, *, background: bool = True) -> str:
        return self._call(VISION, background, lambda: self._inner.vision(jpeg, prompt, background=background))

    def _call(self, kind: str, background: bool, ask: Callable[[], str]) -> str:
        if not self._inner.ready:
            return ask()          # it says "no AI set up" in its own words; nothing to count or retry
        checking = self._let_in(kind, background)
        self._wait_turn()
        started = self._clock.monotonic()
        tries = 1 if checking else self._s.max_retries
        rate_limit_retried = False
        for attempt in range(tries):
            try:
                text = _plain_errors(ask, self._s.error_text_chars)
            except AIUnavailable as err:
                wait = self._wait_before_next_try(kind, err, attempt, background, rate_limit_retried)
                elapsed = self._clock.monotonic() - started
                if wait is None or attempt + 1 >= tries or elapsed + wait > self._time_limit[kind]:
                    self._failed(kind, err)
                    raise
                log.info('AI %s failed (%s); trying again in %g s', kind, err.reason, wait)
                rate_limit_retried = rate_limit_retried or err.rate_limited
                self._clock.sleep(wait)
            else:
                self._worked(kind)
                return text
        raise AssertionError('unreachable: the last try always returns or raises')

    def _let_in(self, kind: str, background: bool) -> bool:
        """Raise AIUnavailable if this call shouldn't be sent now. True if it's the breaker's check call."""
        with self._lock:
            now = self._clock.monotonic()
            shares_quota = kind == CHAT or not self.local_vision
            if background and shares_quota and self._resting_until is not None and now < self._resting_until:
                minutes = max(1, round((self._resting_until - now) / 60))
                raise AIUnavailable(f'{self.label} hit its usage limit, so background chatter is resting for '
                                    f'about {minutes} more minute(s).', rate_limited=True)
            breaker = self._breakers[kind]
            was_open = breaker.open_until is not None
            if not breaker.let_in(now):
                reason = self._problem_now(now) if kind == CHAT else 'Questions about pictures keep failing.'
                raise AIUnavailable(reason or 'The AI keeps failing, so Desk Talk is leaving it alone for a while.')
            return was_open

    def _wait_turn(self) -> None:
        """Keep calls at least min_interval_sec apart."""
        with self._lock:
            now = self._clock.monotonic()
            start = max(now, self._next_turn)
            self._next_turn = start + self._s.min_interval_sec
        if start > now:
            self._clock.sleep(start - now)

    def _wait_before_next_try(self, kind: str, err: AIUnavailable, attempt: int, background: bool,
                              rate_limit_retried: bool) -> float | None:
        """Seconds to wait before trying again, or None to give up now."""
        s = self._s
        if isinstance(err, AIRefused):
            return None
        if err.rate_limited:
            # Trying again uses up more of the quota: only once, and only when you're waiting on a reply.
            if kind == VISION or background or rate_limit_retried:
                return None
            return s.rate_limit_retry_sec
        if kind == VISION:
            return s.vision_retry_sec * (attempt + 1)
        return min(s.retry_start_sec * 2 ** attempt, s.retry_max_sec)

    def _failed(self, kind: str, err: AIUnavailable) -> None:
        with self._lock:
            now = self._clock.monotonic()
            if self._breakers[kind].failed(now):
                log.warning('AI %s failed %d times in a row; leaving it alone for %g s', kind,
                            self._breakers[kind].failures, self._s.breaker_open_sec)
            if err.rate_limited and (kind == CHAT or not self.local_vision):
                self._resting_until = now + self._s.pause_on_limit_sec
                log.warning('Rate limit hit: background AI calls rest for %g min', self._s.pause_on_limit_sec / 60)
            if kind == CHAT or err.rate_limited:
                self._problem = err.reason

    def _worked(self, kind: str) -> None:
        with self._lock:
            self._breakers[kind].worked()
            if kind == CHAT:
                self._problem = None
