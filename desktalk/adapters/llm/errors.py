"""
AI errors in plain words.

Every failure of the AI service becomes an AIUnavailable (desktalk.domain) whose text the app can
show as it is. AIRefused is the kind where trying again can't help (a bad key, a model name that
doesn't exist, a model that can't look at pictures, an Ollama that was already asked again and still
sent nothing), so ResilientChatModel doesn't retry it.
"""

from __future__ import annotations

from desktalk.config.prompts import AiErrors
from desktalk.domain import AIUnavailable

_RATE_LIMIT_WORDS = ('429', 'quota', 'rate limit', 'resource_exhausted', 'exhausted')
_KEY_WORDS = ('401', '403', 'api key', 'api_key', 'unauthorized', 'permission')
_MODEL_WORDS = ('404', 'not found', 'does not exist')
_TIMEOUT_WORDS = ('timed out', 'timeout')
_CONNECT_WORDS = ('connect', 'unreachable', 'name or service')


class AIRefused(AIUnavailable):
    """The AI said no in a way that trying again won't fix."""


def is_rate_limit(err: object) -> bool:
    text = str(err).lower()
    return any(word in text for word in _RATE_LIMIT_WORDS)


def friendly_ai_error(err: object, label: str = '', *, max_chars: int, words: AiErrors) -> str:
    """Turn an AI error into one plain sentence the user can act on. An error it doesn't know is shown as it
    came, cut to `max_chars` letters (settings → llm.error_text_chars). `label` is the AI's name ("Gemini
    gemini-2.5-flash"); `words`: config/prompts.yaml → replies.ai_errors."""
    text = str(err).lower()
    who = label.split(' ')[0] if label else words.the_ai
    ollama = who.lower() == 'ollama'
    if is_rate_limit(text):
        return words.rate_limit.format(who=who)
    if any(word in text for word in _KEY_WORDS):
        return words.bad_key.format(who=who)
    if any(word in text for word in _MODEL_WORDS):
        return words.no_model.format(who=who)
    if any(word in text for word in _TIMEOUT_WORDS):
        return words.ollama_too_slow if ollama else words.too_slow.format(who=who)
    if any(word in text for word in _CONNECT_WORDS):
        return words.ollama_no_connection if ollama else words.no_connection
    return words.other.format(error=str(err)[:max_chars])


def error_from(err: object, label: str = '', *, max_chars: int, words: AiErrors) -> AIUnavailable:
    """The AIUnavailable to raise for an error from the AI service (`max_chars`, `words`: see friendly_ai_error)."""
    text = str(err).lower()
    reason = friendly_ai_error(err, label, max_chars=max_chars, words=words)
    if is_rate_limit(text):
        return AIUnavailable(reason, rate_limited=True)
    if any(word in text for word in _KEY_WORDS + _MODEL_WORDS):
        return AIRefused(reason)
    return AIUnavailable(reason)
