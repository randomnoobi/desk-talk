"""
AI errors in plain words.

Every failure of the AI service becomes an AIUnavailable (desktalk.domain) whose text the app can
show as it is. AIRefused is the kind where trying again can't help (a bad key, a model name that
doesn't exist, a model that can't look at pictures, an Ollama that was already asked again and still
sent nothing), so ResilientChatModel doesn't retry it.
"""

from __future__ import annotations

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


def friendly_ai_error(err: object, label: str = '') -> str:
    """Turn an AI error into one plain sentence the user can act on."""
    text = str(err).lower()
    who = (label or 'the AI').split(' ')[0]
    if is_rate_limit(text):
        return (f'{who} hit its free usage limit. Free plans allow only a few messages per minute '
                f'and a set number per day. Wait a minute and try again; if it keeps happening, '
                f'the daily limit resets tomorrow.')
    if any(word in text for word in _KEY_WORDS):
        return f'{who} rejected the key in .env. Copy the key again, paste it into .env, and restart the server.'
    if any(word in text for word in _MODEL_WORDS):
        return (f'{who} could not find the model named in the settings. Put a current model name in '
                f'config/settings.yaml (llm:) and restart the server.')
    if any(word in text for word in _TIMEOUT_WORDS):
        if who.lower() == 'ollama':
            return 'Ollama took too long to answer (it may still be loading the model). Try again in a moment.'
        return f'{who} took too long to answer. Try again in a moment.'
    if any(word in text for word in _CONNECT_WORDS):
        if who.lower() == 'ollama':
            return 'Could not reach Ollama. Open the Ollama app, then try again.'
        return 'Could not reach the AI service. Check the internet connection and try again.'
    return f'The AI returned an error: {str(err)[:160]}'


def error_from(err: object, label: str = '') -> AIUnavailable:
    """The AIUnavailable to raise for an error from the AI service."""
    text = str(err).lower()
    reason = friendly_ai_error(err, label)
    if is_rate_limit(text):
        return AIUnavailable(reason, rate_limited=True)
    if any(word in text for word in _KEY_WORDS + _MODEL_WORDS):
        return AIRefused(reason)
    return AIUnavailable(reason)
